# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Adithya Shankaran

import getpass
import json
import os
import re
import subprocess
import sys
import uuid
from urllib import error, parse, request


API_BASE_URL = "https://api.storyteller.ai"
# ArtCraft sits behind Cloudflare, which refuses requests carrying Python's default
# user agent (error 1010), so every request names this plugin.
USER_AGENT = "ArtCraft-Plugin/1.0 (+https://github.com/adikshan11/bridgecraft)"
KEYCHAIN_SERVICE = "artcraft-plugin"
# Keychain item name used before the rename; still read so existing setups keep working.
LEGACY_KEYCHAIN_SERVICE = "artcraft-chatgpt-mcp"
MAX_IMAGE_BATCH_COUNT = 4
JOB_TOKEN_PATTERN = re.compile(r"jinf_[A-Za-z0-9_-]{1,100}\Z")
MODEL_PATTERN = re.compile(r"[a-z0-9_]{1,80}\Z")
OPTION_PATTERN = re.compile(r"[a-z0-9_]{1,40}\Z")
MODEL_KINDS = ("image", "video")
MAX_VIDEO_SECONDS = 60
PENDING_JOB_STATES = {"pending", "started", "attempt_failed"}
FAILED_JOB_STATES = {
    "complete_failure",
    "dead",
    "cancelled_by_user",
    "cancelled_by_system",
}


class ArtCraftAPIError(RuntimeError):
    """The ArtCraft API request failed or returned an invalid response."""


class ArtCraftRevoked(ArtCraftAPIError):
    """ArtCraft rejected the exchanged grant. The connector token was not sent."""


class _NoRedirectHandler(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise error.HTTPError(req.full_url, code, msg, headers, fp)


def get_api_key() -> str:
    """Read the ArtCraft API key from the environment or the OS credential store.

    The credential store is the macOS Keychain, Windows Credential Manager, or the
    Secret Service (GNOME Keyring, KWallet) on Linux, reached through keyring.
    """
    if api_key := os.environ.get("ARTCRAFT_API_KEY", "").strip():
        return api_key

    account = getpass.getuser()
    store_failed = False
    for service in (KEYCHAIN_SERVICE, LEGACY_KEYCHAIN_SERVICE):
        try:
            stored = _keyring_password(service, account)
        except ArtCraftAPIError:
            store_failed = True
            break
        if stored is not None:
            return _non_empty(stored)

    # Items made by hand in Keychain Access predate keyring support, and keyring may
    # not be installed, so macOS keeps reading them with the system security tool.
    if sys.platform == "darwin":
        if (stored := _macos_keychain_password(account)) is not None:
            return _non_empty(stored)

    if store_failed:
        raise ArtCraftAPIError(
            "Your system has no credential store this server can use. Set "
            "ARTCRAFT_API_KEY in the environment that starts the server instead."
        )
    raise ArtCraftAPIError(
        f"No ArtCraft API key was found in your {_store_name()}. Save it under the "
        f"service name {KEYCHAIN_SERVICE!r} for your user name, {account!r}, as the "
        "README describes, before generating."
    )


def _store_name() -> str:
    if sys.platform == "darwin":
        return "macOS Keychain"
    if sys.platform == "win32":
        return "Windows Credential Manager"
    return "system keyring"


def _non_empty(api_key: str) -> str:
    if not (api_key := api_key.strip()):
        raise ArtCraftAPIError(f"The ArtCraft API key in your {_store_name()} is empty.")
    return api_key


def _keyring_password(service: str, account: str) -> str | None:
    """The stored password, None if there is none, or ArtCraftAPIError if no store works."""
    try:
        import keyring
        from keyring.errors import KeyringError, NoKeyringError
    except ImportError:
        return None
    try:
        return keyring.get_password(service, account)
    except NoKeyringError as exc:
        raise ArtCraftAPIError("No credential store is available.") from exc
    except KeyringError:
        # A locked or refused store: treat it like a missing item so other sources are tried.
        return None


def _macos_keychain_password(account: str) -> str | None:
    for service in (KEYCHAIN_SERVICE, LEGACY_KEYCHAIN_SERVICE):
        try:
            result = subprocess.run(
                ["/usr/bin/security", "find-generic-password", "-a", account, "-s", service, "-w"],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except FileNotFoundError as exc:
            raise ArtCraftAPIError("The macOS Keychain utility is unavailable.") from exc
        except subprocess.CalledProcessError:
            continue
        except subprocess.TimeoutExpired as exc:
            raise ArtCraftAPIError("Reading the ArtCraft key from Keychain timed out.") from exc
        return result.stdout
    return None


def _reject_connector_token(bearer: str) -> None:
    """The MCP client token authenticates the connector only. It is not an ArtCraft credential."""
    from connector_auth import GrantError, client_token_in_context

    client = client_token_in_context()
    if client and bearer == client:
        raise GrantError("Refusing to send the connector token to ArtCraft.")


def _http_error_message(exc: error.HTTPError, *, exchanged: bool) -> str:
    if exc.code == 401 and exchanged:
        raise ArtCraftRevoked("ArtCraft rejected the grant. Sign in again.")
    if exc.code == 401:
        return "ArtCraft rejected the API key. Check that it is valid and enabled."
    if exc.code == 402:
        return "ArtCraft reported insufficient credits or balance; nothing was generated."
    if exc.code == 403:
        return "ArtCraft API access is not enabled for this account or key."
    if exc.code == 429:
        return "ArtCraft rate-limited this request. Wait before trying again."
    if exc.code == 400:
        try:
            body = json.loads(exc.read())
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = {}
        if isinstance(body, dict):
            detail = body.get("BadInput") or body.get("message")
            if isinstance(detail, str) and detail.strip():
                return f"ArtCraft rejected the request: {detail[:300]}"
        return "ArtCraft rejected the request. Check the model and generation options."
    if 300 <= exc.code < 400:
        return "ArtCraft returned an unexpected redirect; the request was not followed."
    return f"ArtCraft API request failed with HTTP {exc.code}."


def _request_json(path: str, method: str, payload: dict | None = None, auth: bool = True, *,
                  bearer: str | None = None) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    if auth:
        from connector_auth import GrantError

        token = bearer if bearer is not None else get_api_key()
        try:
            _reject_connector_token(token)
        except GrantError as exc:
            raise ArtCraftAPIError(str(exc)) from None
        headers["Authorization"] = f"Bearer {token}"
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = request.Request(
        f"{API_BASE_URL}{path}", data=data, headers=headers, method=method
    )
    try:
        opener = request.build_opener(_NoRedirectHandler())
        with opener.open(req, timeout=30) as response:
            result = json.loads(response.read())
    except ArtCraftRevoked:
        raise
    except error.HTTPError as exc:
        raise ArtCraftAPIError(_http_error_message(exc, exchanged=bearer is not None)) from exc
    except (error.URLError, TimeoutError) as exc:
        raise ArtCraftAPIError("Could not reach the ArtCraft API.") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ArtCraftAPIError("ArtCraft returned an invalid JSON response.") from exc

    if not isinstance(result, dict):
        raise ArtCraftAPIError("ArtCraft returned an unexpected response.")
    return result


def _check_kind(kind: str) -> str:
    if kind not in MODEL_KINDS:
        raise ArtCraftAPIError(f"kind must be one of: {', '.join(MODEL_KINDS)}.")
    return kind


def _check_model(model: str, kind: str) -> str:
    model = model.strip()
    if not MODEL_PATTERN.fullmatch(model):
        raise ArtCraftAPIError(f"Provide a valid ArtCraft {kind} model identifier.")
    return model


def _check_option(name: str, value: str | None) -> str | None:
    if value is not None and not OPTION_PATTERN.fullmatch(value):
        raise ArtCraftAPIError(f"{name} must be one of the option names from list_artcraft_models.")
    return value


def list_library(bearer: str) -> dict:
    """The signed-in user's saved ArtCraft projects. Uses the exchanged token only."""
    result = _request_json("/v1/media_files/project/list", "GET", bearer=bearer)
    rows = result.get("results")
    if result.get("success") is not True or not isinstance(rows, list):
        raise ArtCraftAPIError("ArtCraft did not return a library list.")
    items = []
    for entry in rows[:50]:
        if not isinstance(entry, dict):
            continue
        title = entry.get("maybe_title") if isinstance(entry.get("maybe_title"), str) else ""
        items.append({
            "media_token": entry.get("token") if isinstance(entry.get("token"), str) else "",
            "title": title[:200],
            "project_type": entry.get("project_type") if isinstance(entry.get("project_type"), str) else "",
            "media_class": entry.get("media_class") if isinstance(entry.get("media_class"), str) else "",
            "created_at": entry.get("created_at") if isinstance(entry.get("created_at"), str) else "",
        })
    return {"items": items}


def list_models(kind: str) -> dict:
    """ArtCraft's public model catalogue for one kind of generation (no key needed)."""
    result = _request_json(f"/v1/omni_gen/models/{_check_kind(kind)}", "GET", auth=False)
    models, providers = result.get("models"), result.get("providers")
    if result.get("success") is not True or not isinstance(models, list):
        raise ArtCraftAPIError("ArtCraft did not return a valid model list.")
    # Only models ArtCraft itself provides can be generated with an API key; the
    # rest go through other services' accounts inside the desktop app.
    via_api = {
        entry.get("model")
        for provider in (providers if isinstance(providers, list) else [])
        if isinstance(provider, dict) and provider.get("provider") == "artcraft"
        for entry in provider.get("models") or []
        if isinstance(entry, dict)
    }
    listed = []
    for item in models:
        if not isinstance(item, dict) or not isinstance(item.get("model"), str):
            continue
        listed.append({
            "model": item["model"],
            "name": item.get("full_name") or item["model"],
            "creator": item.get("model_creator"),
            "available_with_api_key": item["model"] in via_api,
            "aspect_ratios": item.get("aspect_ratio_options") or [],
            "default_aspect_ratio": item.get("aspect_ratio_default"),
            "resolutions": item.get("resolution_options") or [],
            "default_resolution": item.get("resolution_default"),
            "min_seconds": item.get("duration_seconds_min"),
            "max_seconds": item.get("duration_seconds_max"),
            "max_batch": item.get("batch_size_max"),
            "start_frame_supported": item.get("starting_keyframe_supported"),
            "sound_supported": item.get("show_generate_with_sound_toggle"),
        })
    return {"kind": kind, "models": listed}


def estimate_cost(
    kind: str,
    model: str,
    *,
    aspect_ratio: str | None = None,
    resolution: str | None = None,
    duration_seconds: int | None = None,
    batch_count: int = 1,
    generate_audio: bool | None = None,
    with_start_frame: bool = False,
) -> dict:
    """What a generation would cost, from ArtCraft's own estimate. Spends nothing."""
    _check_kind(kind)
    payload = {
        "model": _check_model(model, kind),
        "provider": "artcraft",
        "aspect_ratio": _check_option("aspect_ratio", aspect_ratio),
        "resolution": _check_option("resolution", resolution),
    }
    if kind == "image":
        payload["generation_mode"] = {"type": "text_to_image"}
        payload["image_batch_count"] = batch_count
    else:
        payload["generation_mode"] = {"type": "start_frame_to_video" if with_start_frame else "text_to_video"}
        payload["duration_seconds"] = duration_seconds
        payload["video_batch_count"] = batch_count
        payload["generate_audio"] = generate_audio
    result = _request_json(
        f"/v1/generate/cost_estimate/{kind}",
        "POST",
        {key: value for key, value in payload.items() if value is not None},
        auth=False,
    )
    if result.get("success") is not True:
        detail = result.get("error_message")
        raise ArtCraftAPIError(
            f"ArtCraft could not estimate the cost: {detail[:300]}" if isinstance(detail, str)
            else "ArtCraft could not estimate the cost."
        )
    credits, cents = result.get("cost_in_credits"), result.get("cost_in_usd_cents")
    return {
        "model": payload["model"],
        "is_free": result.get("is_free") is True,
        "credits": credits if type(credits) is int else None,
        "usd_cents": cents if type(cents) is int else None,
        "is_rate_limited": result.get("is_rate_limited") is True,
        "has_watermark": result.get("has_watermark") is True,
    }


def check_spend(estimate: dict, max_credits: int | None) -> None:
    """Refuse a paid generation unless the user agreed to at least its cost."""
    if estimate["is_free"]:
        return
    credits = estimate["credits"]
    if credits is None:
        raise ArtCraftAPIError(
            "ArtCraft didn't say what this would cost, so it wasn't started. "
            "Try another model or option."
        )
    price = f" (about ${estimate['usd_cents'] / 100:.2f})" if estimate["usd_cents"] is not None else ""
    if max_credits is None or credits > max_credits:
        raise ArtCraftAPIError(
            f"This would cost {credits} ArtCraft credits{price}, so it wasn't started. "
            f"Ask the user to confirm, then call again with max_credits={credits}."
        )


def _submitted(result: dict, kind: str, model: str, estimate: dict) -> dict:
    job_token = result.get("inference_job_token")
    if result.get("success") is not True or not isinstance(job_token, str):
        raise ArtCraftAPIError(f"ArtCraft did not confirm that the {kind} job was accepted.")
    if not JOB_TOKEN_PATTERN.fullmatch(job_token):
        raise ArtCraftAPIError(f"ArtCraft returned an invalid {kind} job token.")
    return {
        "job_token": job_token,
        "model": model,
        "status": "pending",
        "credits": 0 if estimate["is_free"] else estimate["credits"],
        "message": (
            f"ArtCraft accepted the {kind} request. Use get_artcraft_job with this job "
            f"token to check progress and retrieve the {kind} URL."
        ),
    }


def submit_image_generation(
    prompt: str,
    model: str,
    image_batch_count: int = 1,
    aspect_ratio: str | None = None,
    max_credits: int | None = None,
    *,
    bearer: str | None = None,
) -> dict:
    prompt = prompt.strip()
    model = _check_model(model, "image")
    if not prompt:
        raise ArtCraftAPIError("Provide a non-empty image prompt.")
    if not 1 <= image_batch_count <= MAX_IMAGE_BATCH_COUNT:
        raise ArtCraftAPIError(
            f"image_batch_count must be between 1 and {MAX_IMAGE_BATCH_COUNT}."
        )
    estimate = estimate_cost(
        "image", model, aspect_ratio=aspect_ratio, batch_count=image_batch_count
    )
    check_spend(estimate, max_credits)

    payload = {
        "idempotency_token": str(uuid.uuid4()),
        "model": model,
        "prompt": prompt,
        "image_batch_count": image_batch_count,
    }
    if aspect_ratio:
        payload["aspect_ratio"] = aspect_ratio
    result = _request_json("/v1/omni_api/generate/image", "POST", payload, bearer=bearer)
    return _submitted(result, "image", model, estimate)


def submit_video_generation(
    prompt: str,
    model: str,
    aspect_ratio: str | None = None,
    resolution: str | None = None,
    duration_seconds: int | None = None,
    generate_audio: bool | None = None,
    start_frame_image_url: str | None = None,
    max_credits: int | None = None,
    *,
    bearer: str | None = None,
) -> dict:
    prompt = prompt.strip()
    model = _check_model(model, "video")
    if not prompt:
        raise ArtCraftAPIError("Provide a non-empty video prompt.")
    if duration_seconds is not None and not 1 <= duration_seconds <= MAX_VIDEO_SECONDS:
        raise ArtCraftAPIError(f"duration_seconds must be between 1 and {MAX_VIDEO_SECONDS}.")
    if start_frame_image_url is not None and not start_frame_image_url.startswith("https://"):
        raise ArtCraftAPIError("start_frame_image_url must be an https:// URL.")
    estimate = estimate_cost(
        "video",
        model,
        aspect_ratio=aspect_ratio,
        resolution=resolution,
        duration_seconds=duration_seconds,
        generate_audio=generate_audio,
        with_start_frame=start_frame_image_url is not None,
    )
    check_spend(estimate, max_credits)

    payload = {
        "idempotency_token": str(uuid.uuid4()),
        "model": model,
        "prompt": prompt,
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
        "duration_seconds": duration_seconds,
        "generate_audio": generate_audio,
        "start_frame_image_url": start_frame_image_url,
        "video_batch_count": 1,
    }
    result = _request_json(
        "/v1/omni_api/generate/video",
        "POST",
        {key: value for key, value in payload.items() if value is not None},
        bearer=bearer,
    )
    return _submitted(result, "video", model, estimate)


def get_job(job_token: str, *, bearer: str | None = None) -> dict:
    if not JOB_TOKEN_PATTERN.fullmatch(job_token):
        raise ArtCraftAPIError("Provide a valid ArtCraft job token beginning with jinf_.")

    result = _request_json(
        f"/v1/omni_api/job_status/job/{parse.quote(job_token, safe='')}",
        "GET",
        bearer=bearer,
    )
    state = result.get("state")
    if result.get("success") is not True or not isinstance(state, dict):
        raise ArtCraftAPIError("ArtCraft did not return a valid job status.")

    status_data = state.get("status")
    if not isinstance(status_data, dict) or not isinstance(status_data.get("status"), str):
        raise ArtCraftAPIError("ArtCraft returned an unexpected job status.")
    status = status_data["status"]
    progress = status_data.get("progress_percentage")
    if progress is not None and (
        type(progress) is not int or not 0 <= progress <= 100
    ):
        raise ArtCraftAPIError("ArtCraft returned an invalid job progress percentage.")
    failure_category = status_data.get("maybe_failure_category")
    if failure_category is not None and not isinstance(failure_category, str):
        raise ArtCraftAPIError("ArtCraft returned an invalid job failure category.")

    response = {
        "job_token": job_token,
        "status": status,
        "progress_percentage": progress,
        "media_url": None,
        "failure_category": failure_category,
        "message": None,
    }
    if status == "complete_success":
        media_result = state.get("maybe_result")
        media_links = (
            media_result.get("media_links")
            if isinstance(media_result, dict)
            else None
        )
        media_url = media_links.get("cdn_url") if isinstance(media_links, dict) else None
        if not isinstance(media_url, str) or not media_url.startswith("https://"):
            raise ArtCraftAPIError(
                "ArtCraft marked the job complete but returned no valid HTTPS media URL."
            )
        response["media_url"] = media_url
        response["message"] = "Generation completed."
    elif status in FAILED_JOB_STATES:
        response["message"] = f"Generation ended without a result ({status})."
    elif status in PENDING_JOB_STATES:
        response["message"] = "Generation is still in progress. Check this job again shortly."
    else:
        raise ArtCraftAPIError(f"ArtCraft returned an unrecognized job state: {status}.")

    return response


def get_image_job(job_token: str) -> dict:
    """get_job, with the result under image_url for the older image-only tool."""
    response = get_job(job_token)
    response["image_url"] = response.pop("media_url")
    return response
