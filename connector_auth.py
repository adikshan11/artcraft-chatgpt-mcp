# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Adithya Shankaran

"""Connector sign-in, and the separate ArtCraft grant.

The MCP client token is accepted only for this connector. Its audience is
``https://connect.getartcraft.com/mcp`` and nothing else. Generation and library
reads use a second token, from an exchange at the sign-in issuer, scoped only
to those two uses. That token is stored encrypted under the user's prefix and
nowhere else. It is not logged, not returned to the client, and not sent onward
as the connector token.

Stored files under ``users/<id>/`` are removed 7 days after the session ends.
Deleting a file, disconnecting, or closing the account removes them immediately.
Revoking the grant deletes the exchanged token and ends the session; the stored
files then follow the 7-day clock.
"""

import asyncio
import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib import error, parse, request

from mcp.server.auth.provider import AccessToken

from media_storage import StorageError, grant_key, session_key

log = logging.getLogger(__name__)

CONNECTOR_AUDIENCE = "https://connect.getartcraft.com/mcp"
CONNECTOR_SCOPE = "connector"
EXCHANGED_SCOPES = frozenset({"generation", "library:read"})
ARTCRAFT_API_AUDIENCE = "https://api.storyteller.ai"
TOKEN_EXCHANGE_GRANT = "urn:ietf:params:oauth:grant-type:token-exchange"
ACCESS_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:access_token"
GENERATION_LIMIT_PER_HOUR = 30
LIBRARY_LIMIT_PER_HOUR = 120
STATUS_LIMIT_PER_HOUR = 120
_LIMITS = {
    "generation": GENERATION_LIMIT_PER_HOUR,
    "library": LIBRARY_LIMIT_PER_HOUR,
    "status": STATUS_LIMIT_PER_HOUR,
}

_lock = threading.Lock()
_ephemeral_key: bytes | None = None
_calls: dict[tuple[str, str], list[float]] = {}


class GrantError(RuntimeError):
    """A user-facing problem with the ArtCraft grant. The message has no token in it."""


def client_token_in_context() -> str | None:
    """The connector token on this request, if the caller presented one."""
    from mcp.server.auth.middleware.auth_context import get_access_token

    token = get_access_token()
    if token is None or not token.token:
        return None
    return token.token


def reject_client_token(bearer: str | None) -> None:
    """Refuse to attach the connector token to any upstream call."""
    client = client_token_in_context()
    if bearer and client and bearer == client:
        raise GrantError("Refusing to send the connector token upstream.")


def scopes_of(claims: dict) -> list[str]:
    scope = claims.get("scope")
    if scope is None:
        scope = claims.get("scp") or ""
    if isinstance(scope, str):
        return [item for item in scope.split() if item]
    if isinstance(scope, list):
        return [str(item) for item in scope if isinstance(item, str) and item]
    return []


def access_token_from_jwt(token: str, key, issuer: str) -> AccessToken | None:
    """Validate issuer, signature, expiry, audience and scopes. Audience must be exact."""
    import jwt

    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256", "ES256", "PS256"],
            issuer=issuer,
            options={"require": ["exp", "iss", "sub", "aud"], "verify_aud": False},
        )
    except jwt.PyJWTError:
        return None
    # A list, or any other audience next to the connector's, is rejected.
    if claims.get("aud") != CONNECTOR_AUDIENCE:
        return None
    scopes = scopes_of(claims)
    if CONNECTOR_SCOPE not in scopes:
        return None
    return AccessToken(
        token=token,
        client_id=str(claims.get("azp") or claims.get("client_id") or ""),
        scopes=scopes,
        expires_at=int(claims["exp"]),
        resource=CONNECTOR_AUDIENCE,
        subject=str(claims["sub"]),
        claims={"iss": claims["iss"]},
    )


def _fernet():
    from cryptography.fernet import Fernet

    configured = os.environ.get("GRANT_KEY", "").strip()
    if configured:
        try:
            return Fernet(configured.encode())
        except (ValueError, TypeError) as exc:
            raise StorageError("GRANT_KEY is not a valid encryption key.") from exc
    if os.environ.get("RENDER"):
        raise StorageError("GRANT_KEY is not set, so Studio cannot keep an ArtCraft grant.")
    global _ephemeral_key
    with _lock:
        if _ephemeral_key is None:
            _ephemeral_key = Fernet.generate_key()
        return Fernet(_ephemeral_key)


def save_grant(store, user_id: str, token: str, expires_at: int) -> None:
    """Encrypt the exchanged token and store it under this user's prefix."""
    if not token or any(c in token for c in "\r\n"):
        raise GrantError("The ArtCraft token could not be stored.")
    payload = json.dumps({"token": token, "expires_at": int(expires_at)}, separators=(",", ":")).encode()
    store.put_private(grant_key(user_id), _fernet().encrypt(payload))


def load_grant(store, user_id: str, now: datetime | None = None) -> str | None:
    """The stored token, or None when it is missing, expired, or unreadable.

    An expired or unreadable grant is deleted. The token is not logged.
    """
    raw = store.get_private(grant_key(user_id))
    if raw is None:
        return None
    now = now or datetime.now(timezone.utc)
    try:
        data = json.loads(_fernet().decrypt(raw))
        token = data["token"]
        expires_at = int(data["expires_at"])
    except Exception:
        delete_grant(store, user_id)
        return None
    if not isinstance(token, str) or not token or expires_at <= int(now.timestamp()):
        delete_grant(store, user_id)
        return None
    return token


def delete_grant(store, user_id: str) -> None:
    store.delete_private(grant_key(user_id))


def _issuer_host(url: str) -> str | None:
    parts = parse.urlparse(url)
    if parts.scheme != "https" or not parts.hostname:
        return None
    return parts.hostname


def exchange_for_artcraft(subject_token: str, issuer: str) -> tuple[str, int]:
    """Exchange the connector token at the issuer for a generation and library token.

    The subject token is presented only to the issuer's token endpoint. The
    returned token must be a different value, scoped only to generation and
    library read. Nothing about the token is logged.
    """
    token_url = os.environ.get("OAUTH_TOKEN_URL", "").strip() or f"{issuer.rstrip('/')}/oauth/token"
    if _issuer_host(token_url) is None or _issuer_host(token_url) != _issuer_host(issuer):
        raise GrantError("Token exchange only talks to the sign-in issuer.")
    body = parse.urlencode({
        "grant_type": TOKEN_EXCHANGE_GRANT,
        "subject_token": subject_token,
        "subject_token_type": ACCESS_TOKEN_TYPE,
        "requested_token_type": ACCESS_TOKEN_TYPE,
        "audience": ARTCRAFT_API_AUDIENCE,
        "scope": "generation library:read",
    }).encode()
    req = request.Request(
        token_url,
        data=body,
        headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded",
                 "User-Agent": "BridgeCraft/1.0"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=20) as response:
            result = json.loads(response.read())
    except error.HTTPError as exc:
        log.warning("ArtCraft token exchange failed with HTTP %s.", exc.code)
        raise GrantError("ArtCraft sign-in could not be completed.") from None
    except (error.URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError):
        log.warning("ArtCraft token exchange did not return a token.")
        raise GrantError("ArtCraft sign-in could not be completed.") from None
    if not isinstance(result, dict):
        raise GrantError("ArtCraft sign-in could not be completed.")
    token = result.get("access_token")
    scopes = set(scopes_of(result))
    expires_in = result.get("expires_in")
    if not isinstance(token, str) or not token or token == subject_token:
        raise GrantError("The sign-in server did not return a separate ArtCraft token.")
    if scopes != EXCHANGED_SCOPES:
        raise GrantError("The ArtCraft token must be scoped only to generation and library read.")
    if type(expires_in) is not int or expires_in < 1 or expires_in > 86400 * 30:
        raise GrantError("The ArtCraft token did not include an expiry.")
    return token, int(time.time()) + expires_in


def reset_rate_limits() -> None:
    with _lock:
        _calls.clear()


def check_rate(user_id: str, purpose: str, now: datetime | None = None) -> None:
    limit = _LIMITS.get(purpose)
    if limit is None:
        raise GrantError("Unknown ArtCraft request.")
    now = now or datetime.now(timezone.utc)
    window = now.timestamp() - 3600
    with _lock:
        stamps = [stamp for stamp in _calls.get((user_id, purpose), []) if stamp >= window]
        if len(stamps) >= limit:
            _calls[(user_id, purpose)] = stamps
            raise GrantError("Too many ArtCraft requests this hour. Try again later.")
        stamps.append(now.timestamp())
        _calls[(user_id, purpose)] = stamps


def artcraft_bearer(store, user_id: str, subject_token: str, issuer: str, purpose: str) -> str:
    """The exchanged token for generation or a library read. Never the connector token."""
    if purpose not in _LIMITS:
        raise GrantError("Unknown ArtCraft request.")
    if not subject_token or not issuer:
        raise GrantError("Sign in with ArtCraft before using generation or the library.")
    check_rate(user_id, purpose)
    current = load_grant(store, user_id)
    if current:
        reject_client_token(current)
        return current
    token, expires_at = exchange_for_artcraft(subject_token, issuer)
    reject_client_token(token)
    save_grant(store, user_id, token, expires_at)
    return token


def _parse(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _read_session(store, user_id: str) -> dict | None:
    raw = store.get_private(session_key(user_id))
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("last_active"), str):
        return None
    return {"last_active": data["last_active"], "ended_at": data.get("ended_at")}


def _write_session(store, user_id: str, state: dict) -> None:
    body = json.dumps({"last_active": state["last_active"], "ended_at": state.get("ended_at")},
                      separators=(",", ":")).encode()
    store.put_private(session_key(user_id), body)


def note_activity(store, user_id: str, now: datetime | None = None) -> None:
    """The signed-in session is active again, so the retention clock is cleared."""
    now = now or datetime.now(timezone.utc)
    _write_session(store, user_id, {"last_active": now.isoformat(), "ended_at": None})


def mark_session_ended(store, user_id: str, now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    state = _read_session(store, user_id) or {"last_active": now.isoformat(), "ended_at": None}
    if not state.get("ended_at"):
        state["ended_at"] = now.isoformat()
    _write_session(store, user_id, state)


def _newest_media_at(store, user_id: str) -> datetime | None:
    stamps = []
    for obj in store.list(user_id):
        try:
            stamps.append(_parse(obj.modified_at))
        except ValueError:
            continue
    return max(stamps) if stamps else None


def expire_stored_files(store, now: datetime, retention_days: int, idle_seconds: int) -> int:
    """Delete every file under users whose session ended at least ``retention_days`` ago.

    A session ends after ``idle_seconds`` without a request. A user with no
    session record is treated as last active when their newest file was written.
    An active session keeps its files, however old those files are.
    """
    deleted = 0
    idle = timedelta(seconds=idle_seconds)
    keep = timedelta(days=retention_days)
    for user_id in store.list_user_ids():
        state = _read_session(store, user_id)
        if state is None:
            newest = _newest_media_at(store, user_id)
            if newest is None:
                continue
            state = {"last_active": newest.isoformat(), "ended_at": None}
            _write_session(store, user_id, state)
        try:
            last = _parse(state["last_active"])
            ended = _parse(state["ended_at"]) if state.get("ended_at") else None
        except (TypeError, ValueError):
            continue
        if ended is None and now - last >= idle:
            ended = last
            state["ended_at"] = ended.isoformat()
            _write_session(store, user_id, state)
        if ended is not None and now - ended >= keep:
            deleted += store.delete_user(user_id)
    return deleted


def sweep_expired_grants(store, now: datetime) -> int:
    removed = 0
    for user_id in store.list_user_ids():
        if store.get_private(grant_key(user_id)) is None:
            continue
        if load_grant(store, user_id, now) is None:
            removed += 1
    return removed


async def revoke(store, user_id: str) -> None:
    """Drop the exchanged token and end the session. Stored files follow the 7-day clock."""
    def wipe():
        delete_grant(store, user_id)
        mark_session_ended(store, user_id)

    await asyncio.to_thread(wipe)
    import craft_sessions
    await craft_sessions.manager.close_user(user_id)


def forget_user_files(store, user_id: str) -> int:
    """Disconnect or account close: delete the grant and every stored file now."""
    delete_grant(store, user_id)
    deleted = store.delete_user(user_id)
    import craft_sessions
    craft_sessions.manager.close_user_now(user_id)
    return deleted


async def forget_user(store, user_id: str) -> int:
    return await asyncio.to_thread(forget_user_files, store, user_id)
