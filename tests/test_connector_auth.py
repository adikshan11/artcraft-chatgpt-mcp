# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Adithya Shankaran

"""Audience checks, token separation, and file deletion for the hosted connector."""

import io
import logging
import os
import shutil
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib import error

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric import rsa
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken

import artcraft_api
import connector_auth
import log_redaction
from artcraft_api import ArtCraftAPIError
from media_storage import LocalStorage, S3Storage, StorageError, grant_key, object_key, reject_connector_token, user_id_for

ISSUER = "https://artcraft-studio.us.auth0.com/"
AUDIENCE = "https://connect.getartcraft.com/mcp"
CONNECTOR = "connector-token-not-for-upstream"
EXCHANGED = "exchanged-token-for-artcraft"


def _private_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwt(key, **overrides):
    import jwt

    claims = {
        "iss": ISSUER,
        "sub": "auth0|person",
        "aud": AUDIENCE,
        "exp": int(time.time()) + 600,
        "scope": "connector",
    }
    claims.update(overrides)
    return jwt.encode(claims, key, algorithm="RS256")


class AudienceTests(unittest.TestCase):
    def setUp(self):
        self.key = _private_key()
        self.public = self.key.public_key()

    def test_rejects_a_token_for_any_other_audience(self):
        for audience in (
            "https://bridgecraft.onrender.com/studio",
            "https://api.storyteller.ai",
            [AUDIENCE, "https://api.storyteller.ai"],
            "",
        ):
            token = _jwt(self.key, aud=audience)
            self.assertIsNone(
                connector_auth.access_token_from_jwt(token, self.public, ISSUER),
                audience,
            )

    def test_rejects_a_bad_issuer_expiry_or_scope(self):
        other = _private_key()
        self.assertIsNone(connector_auth.access_token_from_jwt(_jwt(other), self.public, ISSUER))
        self.assertIsNone(connector_auth.access_token_from_jwt(
            _jwt(self.key, iss="https://other.example/"), self.public, ISSUER))
        self.assertIsNone(connector_auth.access_token_from_jwt(
            _jwt(self.key, exp=int(time.time()) - 10), self.public, ISSUER))
        self.assertIsNone(connector_auth.access_token_from_jwt(
            _jwt(self.key, aud=AUDIENCE + "/"), self.public, ISSUER))
        self.assertIsNone(connector_auth.access_token_from_jwt(
            _jwt(self.key, scope="generation library:read"), self.public, ISSUER))
        self.assertIsNone(connector_auth.access_token_from_jwt(
            _jwt(self.key, scope=""), self.public, ISSUER))
        with patch.dict(os.environ, {"OAUTH_AUDIENCE": "https://api.storyteller.ai"}):
            self.assertIsNone(connector_auth.access_token_from_jwt(
                _jwt(self.key, aud="https://api.storyteller.ai"), self.public, ISSUER))

    def test_accepts_the_connector_audience_only(self):
        token = _jwt(self.key)
        access = connector_auth.access_token_from_jwt(token, self.public, ISSUER)
        self.assertIsNotNone(access)
        self.assertEqual(access.resource, AUDIENCE)
        self.assertIn("connector", access.scopes)
        self.assertEqual(access.subject, "auth0|person")


class NoPassthroughTests(unittest.TestCase):
    def setUp(self):
        access = AccessToken(token=CONNECTOR, client_id="c", scopes=["connector"], subject="person")
        self.token = auth_context_var.set(AuthenticatedUser(access))

    def tearDown(self):
        auth_context_var.reset(self.token)

    def test_artcraft_call_refuses_the_connector_token(self):
        with patch("artcraft_api.request.build_opener") as build:
            with self.assertRaises(ArtCraftAPIError) as raised:
                artcraft_api._request_json("/v1/omni_api/generate/image", "POST", {"prompt": "x"}, bearer=CONNECTOR)
        self.assertIn("connector token", str(raised.exception))
        build.assert_not_called()

    def test_artcraft_call_sends_the_exchanged_token_only(self):
        class Response:
            def read(self):
                return b'{"success": true, "results": []}'

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        opener = MagicMock()
        opener.open.return_value = Response()
        with patch("artcraft_api.request.build_opener", return_value=opener):
            artcraft_api.list_library(EXCHANGED)
        request = opener.open.call_args.args[0]
        authorization = request.get_header("Authorization")
        self.assertEqual(authorization, f"Bearer {EXCHANGED}")
        self.assertNotIn(CONNECTOR, authorization)
        self.assertNotIn(CONNECTOR.encode(), request.data or b"")

    def test_github_refuses_the_connector_token(self):
        from app import GitHubRequestError, github_get

        with patch.dict(os.environ, {"GITHUB_TOKEN": CONNECTOR}):
            with self.assertRaises(GitHubRequestError):
                github_get("/orgs/storytold/repos")

    def test_storage_call_does_not_carry_the_connector_token(self):
        class FakeClient:
            def __init__(self):
                self.calls = []

            def upload_file(self, *args, **kwargs):
                self.calls.append((args, kwargs))

        store = S3Storage.__new__(S3Storage)
        store.bucket, store.client = "bucket", FakeClient()
        source = Path(tempfile.mkdtemp()) / "a.png"
        source.write_bytes(b"png-bytes")
        self.addCleanup(shutil.rmtree, source.parent)
        user = user_id_for(ISSUER, "person")
        media_id = f"{'ab' * 16}.png"
        store.put_file(user, media_id, source)
        recorded = repr(store.client.calls)
        self.assertNotIn(CONNECTOR, recorded)
        self.assertIn(object_key(user, media_id), store.client.calls[0][0])
        with self.assertRaises(StorageError):
            reject_connector_token(f"users/{CONNECTOR}")


class GrantAndDeletionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = LocalStorage(self.tmp)
        self.addCleanup(shutil.rmtree, self.tmp)
        self.key = Fernet.generate_key().decode()
        self.env = patch.dict(os.environ, {"GRANT_KEY": self.key})
        self.env.start()
        self.addCleanup(self.env.stop)
        connector_auth.reset_rate_limits()

    def test_grant_is_encrypted_and_absent_from_logs(self):
        user = user_id_for(ISSUER, "person")
        connector_auth.save_grant(self.store, user, EXCHANGED, int(time.time()) + 60)
        blob = self.store.get_private(grant_key(user))
        self.assertIsNotNone(blob)
        self.assertNotIn(EXCHANGED.encode(), blob)
        self.assertEqual(connector_auth.load_grant(self.store, user), EXCHANGED)

        log_redaction.install()
        buffer = io.StringIO()
        handler = logging.StreamHandler(buffer)
        logger = logging.getLogger("connector_auth")
        logger.addHandler(handler)
        logger.setLevel(logging.WARNING)
        self.addCleanup(logger.removeHandler, handler)
        with patch("connector_auth.request.urlopen", side_effect=error.HTTPError(
            "https://artcraft-studio.us.auth0.com/oauth/token", 400, "bad", hdrs=None, fp=None
        )):
            with self.assertRaises(connector_auth.GrantError):
                connector_auth.exchange_for_artcraft(EXCHANGED, ISSUER)
        self.assertNotIn(EXCHANGED, buffer.getvalue())

    def test_disconnect_deletes_files_immediately(self):
        user = user_id_for(ISSUER, "person")
        source = self.tmp / "src.png"
        source.write_bytes(b"x")
        media_id = f"{'cd' * 16}.png"
        self.store.put_file(user, media_id, source)
        connector_auth.save_grant(self.store, user, EXCHANGED, int(time.time()) + 60)
        connector_auth.note_activity(self.store, user)
        deleted = connector_auth.forget_user_files(self.store, user)
        self.assertGreaterEqual(deleted, 1)
        self.assertFalse((self.tmp / object_key(user, media_id)).exists())
        self.assertIsNone(connector_auth.load_grant(self.store, user))

    def test_files_remain_until_seven_days_after_the_session_ends(self):
        user = user_id_for(ISSUER, "person")
        source = self.tmp / "src.png"
        source.write_bytes(b"x")
        media_id = f"{'ef' * 16}.png"
        self.store.put_file(user, media_id, source)
        now = datetime.now(timezone.utc)
        ended = (now - timedelta(days=6)).isoformat()
        connector_auth._write_session(self.store, user, {"last_active": ended, "ended_at": ended})
        self.assertEqual(connector_auth.expire_stored_files(self.store, now, 7, 600), 0)
        self.assertTrue((self.tmp / object_key(user, media_id)).exists())
        ended = (now - timedelta(days=8)).isoformat()
        connector_auth._write_session(self.store, user, {"last_active": ended, "ended_at": ended})
        self.assertGreaterEqual(connector_auth.expire_stored_files(self.store, now, 7, 600), 1)
        self.assertFalse((self.tmp / object_key(user, media_id)).exists())

    def test_render_refuses_a_grant_without_a_key(self):
        user = user_id_for(ISSUER, "person")
        with patch.dict(os.environ, {"RENDER": "true", "GRANT_KEY": ""}):
            with self.assertRaises(StorageError):
                connector_auth.save_grant(self.store, user, EXCHANGED, int(time.time()) + 60)

    def test_rate_limit_stops_a_burst_of_generation(self):
        for _ in range(connector_auth.GENERATION_LIMIT_PER_HOUR):
            connector_auth.check_rate("person", "generation")
        with self.assertRaises(connector_auth.GrantError):
            connector_auth.check_rate("person", "generation")


def _json_response(body: bytes):
    class Response:
        def read(self):
            return body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    return Response()


class RequestPathTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = LocalStorage(self.tmp)
        self.addCleanup(shutil.rmtree, self.tmp)
        self.key = Fernet.generate_key().decode()
        connector_auth.reset_rate_limits()
        import studio_mcp
        studio_mcp.storage.cache_clear()
        self.studio_mcp = studio_mcp

    def _access(self, token=CONNECTOR):
        return AccessToken(
            token=token, client_id="c", scopes=["connector"], expires_at=int(time.time()) + 600,
            resource=AUDIENCE, subject="user-1", claims={"iss": ISSUER},
        )

    async def test_generation_sends_the_exchanged_token_and_not_the_connector_token(self):
        from mcp import Client

        access = self._access()
        ctx = auth_context_var.set(AuthenticatedUser(access))
        self.addCleanup(auth_context_var.reset, ctx)
        artcraft = MagicMock()

        def open_artcraft(req, timeout=30):
            url = req.full_url
            body = req.data or b""
            self.assertNotIn(CONNECTOR.encode(), body)
            self.assertNotIn(CONNECTOR, req.get_header("Authorization") or "")
            if url.endswith("/v1/generate/cost_estimate/image"):
                self.assertIsNone(req.get_header("Authorization"))
                return _json_response(
                    b'{"success":true,"is_free":true,"cost_in_credits":0,"cost_in_usd_cents":0,'
                    b'"is_rate_limited":false,"has_watermark":false}'
                )
            if url.endswith("/v1/omni_api/generate/image"):
                self.assertEqual(req.get_header("Authorization"), f"Bearer {EXCHANGED}")
                self.assertTrue(url.startswith("https://api.storyteller.ai/"))
                return _json_response(b'{"success":true,"inference_job_token":"jinf_job1"}')
            raise AssertionError(url)

        artcraft.open.side_effect = open_artcraft

        def open_issuer(req, timeout=20):
            self.assertEqual(req.full_url, f"{ISSUER}oauth/token")
            self.assertIn(CONNECTOR.encode(), req.data)
            self.assertNotIn(b"api.storyteller.ai", req.full_url.encode())
            return _json_response(
                b'{"access_token":"%s","scope":"generation library:read","expires_in":3600}' % EXCHANGED.encode()
            )

        log_redaction.install()
        buffer = io.StringIO()
        handler = logging.StreamHandler(buffer)
        root = logging.getLogger()
        root.addHandler(handler)
        self.addCleanup(root.removeHandler, handler)
        with (
            patch.dict(os.environ, {"GRANT_KEY": self.key, "OAUTH_TOKEN_URL": ""}),
            patch("studio_mcp.storage", return_value=self.store),
            patch("connector_auth.request.urlopen", side_effect=open_issuer),
            patch("artcraft_api.request.build_opener", return_value=artcraft),
        ):
            async with Client(self.studio_mcp.studio) as client:
                result = await client.call_tool(
                    "generate_artcraft_image", {"prompt": "a red square", "model": "flux_1_schnell"}
                )
        self.assertFalse(result.is_error)
        rendered = str(result.structured_content)
        self.assertNotIn(CONNECTOR, rendered)
        self.assertNotIn(EXCHANGED, rendered)
        user = user_id_for(ISSUER, "user-1")
        blob = self.store.get_private(grant_key(user))
        self.assertIsNotNone(blob)
        self.assertNotIn(EXCHANGED.encode(), blob)
        self.assertNotIn(CONNECTOR.encode(), blob)
        logged = buffer.getvalue()
        self.assertNotIn(CONNECTOR, logged)
        self.assertNotIn(EXCHANGED, logged)

    async def test_revocation_deletes_the_grant_and_keeps_files(self):
        from mcp import Client

        user = user_id_for(ISSUER, "user-1")
        source = self.tmp / "src.png"
        source.write_bytes(b"png")
        media_id = f"{'ab' * 16}.png"
        self.store.put_file(user, media_id, source)
        with patch.dict(os.environ, {"GRANT_KEY": self.key}):
            connector_auth.save_grant(self.store, user, EXCHANGED, int(time.time()) + 60)
        connector_auth.note_activity(self.store, user)
        access = self._access()
        ctx = auth_context_var.set(AuthenticatedUser(access))
        self.addCleanup(auth_context_var.reset, ctx)
        opener = MagicMock()

        def open_artcraft(req, timeout=30):
            raise error.HTTPError(req.full_url, 401, "no", hdrs=None, fp=io.BytesIO(b"{}"))

        opener.open.side_effect = open_artcraft
        with (
            patch.dict(os.environ, {"GRANT_KEY": self.key}),
            patch("studio_mcp.storage", return_value=self.store),
            patch("artcraft_api.request.build_opener", return_value=opener),
        ):
            async with Client(self.studio_mcp.studio) as client:
                result = await client.call_tool("list_artcraft_library", {})
        self.assertTrue(result.is_error)
        self.assertIsNone(connector_auth.load_grant(self.store, user))
        self.assertTrue((self.tmp / object_key(user, media_id)).exists())
        state = connector_auth._read_session(self.store, user)
        self.assertIsNotNone(state.get("ended_at"))

    def test_account_route_deletes_files_for_the_connector_audience_only(self):
        from app import app

        user = user_id_for(ISSUER, "user-1")
        source = self.tmp / "src.png"
        source.write_bytes(b"png")
        media_id = f"{'cd' * 16}.png"
        self.store.put_file(user, media_id, source)
        private = _private_key()
        public = private.public_key()
        good = _jwt(private, sub="user-1")
        wrong = _jwt(private, sub="user-1", aud="https://api.storyteller.ai")
        verifier = self.studio_mcp.JwtTokenVerifier(ISSUER, f"{ISSUER}.well-known/jwks.json")
        with (
            patch.object(verifier._jwks, "get_signing_key_from_jwt", return_value=MagicMock(key=public)),
            patch("studio_mcp._verifier", verifier),
            patch("studio_mcp.storage", return_value=self.store),
        ):
            client = app.test_client()
            refused = client.post("/account/disconnect", headers={"Authorization": f"Bearer {wrong}"})
            self.assertEqual(refused.status_code, 401)
            self.assertTrue((self.tmp / object_key(user, media_id)).exists())
            done = client.post("/account/disconnect", headers={"Authorization": f"Bearer {good}"})
        self.assertEqual(done.status_code, 200)
        self.assertGreater(done.get_json()["deleted_files"], 0)
        self.assertNotIn(good, done.get_data(as_text=True))
        self.assertFalse((self.tmp / object_key(user, media_id)).exists())

    def test_model_list_sends_no_token(self):
        access = self._access()
        ctx = auth_context_var.set(AuthenticatedUser(access))
        self.addCleanup(auth_context_var.reset, ctx)
        opener = MagicMock()

        def open_artcraft(req, timeout=30):
            self.assertIsNone(req.get_header("Authorization"))
            self.assertNotIn(CONNECTOR.encode(), req.data or b"")
            return _json_response(
                b'{"success":true,"models":[{"model":"flux_1_schnell","full_name":"Flux"}],"providers":[]}'
            )

        opener.open.side_effect = open_artcraft
        with patch("artcraft_api.request.build_opener", return_value=opener):
            listed = artcraft_api.list_models("image")
        self.assertEqual(listed["models"][0]["model"], "flux_1_schnell")

    async def test_file_contents_are_not_logged(self):
        from mcp import Client
        from pypdf import PdfWriter

        sentinel = "SENTINELFILECONTENTS9F3C"
        user = user_id_for(ISSUER, "user-1")
        pdf = self.tmp / "note.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        writer.add_metadata({"/Title": sentinel})
        with pdf.open("wb") as handle:
            writer.write(handle)
        self.assertIn(sentinel.encode(), pdf.read_bytes())
        media_id = f"{'ef' * 16}.pdf"
        self.store.put_file(user, media_id, pdf)
        access = self._access()
        ctx = auth_context_var.set(AuthenticatedUser(access))
        self.addCleanup(auth_context_var.reset, ctx)
        log_redaction.install()
        buffer = io.StringIO()
        handler = logging.StreamHandler(buffer)
        root = logging.getLogger()
        previous = root.level
        root.setLevel(logging.DEBUG)
        root.addHandler(handler)
        self.addCleanup(root.removeHandler, handler)
        self.addCleanup(root.setLevel, previous)
        with patch("studio_mcp.storage", return_value=self.store):
            async with Client(self.studio_mcp.studio) as client:
                await client.call_tool("read_pdf_text", {"media_id": media_id})
        self.assertNotIn(sentinel, buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
