import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mcp import Client
from mcp.server.auth.provider import AccessToken
from mcp.server.mcpserver.exceptions import ToolError
from PIL import Image
from pypdf import PdfReader, PdfWriter
from starlette.testclient import TestClient as AsgiTestClient

import media_engine as engine
import studio_mcp
from media_storage import LocalStorage, StorageError, object_key, user_id_for
from studio_mcp import studio

HAS_FFMPEG = shutil.which("ffmpeg") is not None


def make_pdf(path: Path, pages: int = 3):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    with path.open("wb") as handle:
        writer.write(handle)


class StudioTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = LocalStorage(self.tmp / "store")
        studio_mcp.storage.cache_clear()
        self.patches = [
            patch("studio_mcp.storage", return_value=self.store),
            patch.dict(os.environ, {"STUDIO_DEV_USER": "alice"}),
        ]
        for p in self.patches:
            p.start()
        self.user = user_id_for("local-dev", "alice")

    def tearDown(self):
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.tmp)

    def add(self, path: Path, user=None) -> str:
        media_id = f"{'a' * 31}{len(self.store.list(user or self.user))}.{path.suffix[1:]}"
        self.store.put_file(user or self.user, media_id, path)
        return media_id

    async def call(self, name, **arguments):
        async with Client(studio) as client:
            result = await client.call_tool(name, arguments)
        if result.is_error:
            raise ToolError(result.content[0].text)
        return result.structured_content


class IdentityAndIsolationTests(StudioTestCase):
    async def test_requires_sign_in(self):
        with patch.dict(os.environ, {"STUDIO_DEV_USER": ""}):
            with self.assertRaisesRegex(ToolError, "Sign in"):
                await self.call("list_media")

    async def test_oauth_subject_scopes_storage(self):
        token = AccessToken(token="t", client_id="c", scopes=[], subject="user-1", claims={"iss": "https://idp"})
        with patch("studio_mcp.get_access_token", return_value=token):
            self.assertEqual(studio_mcp.current_user(), user_id_for("https://idp", "user-1"))

    async def test_cannot_read_another_users_media(self):
        image = self.tmp / "x.png"
        Image.new("RGB", (10, 10)).save(image)
        bob = user_id_for("local-dev", "bob")
        media_id = self.add(image, user=bob)
        with self.assertRaisesRegex(ToolError, "not found"):
            await self.call("inspect_media", media_id=media_id)

    def test_media_ids_cannot_traverse_paths(self):
        for bad in ("../x.png", "a/b.png", "x" * 32 + ".exe", ""):
            with self.assertRaises(StorageError):
                object_key(self.user, bad)

    def test_storage_config_supports_backblaze_and_r2(self):
        from media_storage import S3Storage, storage_from_env

        b2 = {"S3_ENDPOINT_URL": "https://s3.us-west-004.backblazeb2.com", "S3_ACCESS_KEY_ID": "k",
              "S3_SECRET_ACCESS_KEY": "s", "S3_BUCKET": "b"}
        with patch.dict(os.environ, b2, clear=True):
            store = storage_from_env()
            self.assertIsInstance(store, S3Storage)
            self.assertEqual(store.client.meta.region_name, "us-west-004")
            self.assertIn("backblazeb2.com", store.client.meta.endpoint_url)
        r2 = {"R2_ACCOUNT_ID": "acct", "R2_ACCESS_KEY_ID": "k", "R2_SECRET_ACCESS_KEY": "s", "R2_BUCKET": "b"}
        with patch.dict(os.environ, r2, clear=True):
            self.assertEqual(storage_from_env().client.meta.endpoint_url, "https://acct.r2.cloudflarestorage.com")
        with patch.dict(os.environ, {"RENDER": "true"}, clear=True):
            with self.assertRaises(StorageError):
                storage_from_env()

    def test_url_import_blocks_private_networks(self):
        for url in ("http://example.com/a.png", "https://127.0.0.1/a.png", "https://169.254.169.254/x.png"):
            with self.assertRaises(engine.MediaError):
                engine.fetch_url(url, self.tmp / "out.png", 1000)

    async def test_signed_out_http_requests_get_oauth_challenge(self):
        from mcp.server.auth.settings import AuthSettings
        from studio_mcp import JwtTokenVerifier

        server = studio_mcp.MCPServer(
            "t",
            token_verifier=JwtTokenVerifier("https://idp.example", "https://site.example/studio",
                                            "https://idp.example/jwks.json"),
            auth=AuthSettings(issuer_url="https://idp.example", resource_server_url="https://site.example/studio",
                              validate_token_resource=False),
        )
        app = server.streamable_http_app(streamable_http_path="/studio")
        with AsgiTestClient(app) as client:
            response = client.post("/studio", json={}, headers={"Host": "localhost"})
            self.assertEqual(response.status_code, 401)
            self.assertIn("resource_metadata", response.headers["www-authenticate"])
            metadata = client.get("/.well-known/oauth-protected-resource/studio").json()
            self.assertEqual(metadata["authorization_servers"], ["https://idp.example"])


class ImageTests(StudioTestCase):
    async def test_edit_image_pipeline(self):
        source = self.tmp / "photo.png"
        Image.new("RGB", (400, 300), "red").save(source)
        media_id = self.add(source)
        result = await self.call(
            "edit_image",
            media_id=media_id,
            operations=[
                {"op": "crop", "left": 0, "top": 0, "right": 200, "bottom": 200},
                {"op": "resize", "width": 100},
                {"op": "brightness", "factor": 1.2},
                {"op": "text", "text": "Hello", "x": 5, "y": 5},
                {"op": "grayscale"},
            ],
            output_format="jpg",
        )
        self.assertTrue(result["media_id"].endswith(".jpg"))
        out = self.tmp / "out.jpg"
        self.store.get_file(self.user, result["media_id"], out)
        with Image.open(out) as image:
            self.assertEqual(image.size, (100, 100))

    async def test_rejects_unknown_operation(self):
        source = self.tmp / "photo.png"
        Image.new("RGB", (10, 10)).save(source)
        with self.assertRaisesRegex(ToolError, "Unknown image operation"):
            await self.call("edit_image", media_id=self.add(source), operations=[{"op": "explode"}])


class PdfTests(StudioTestCase):
    async def test_organize_merge_and_sign(self):
        a, b = self.tmp / "a.pdf", self.tmp / "b.pdf"
        make_pdf(a, 3)
        make_pdf(b, 2)
        a_id, b_id = self.add(a), self.add(b)

        organized = await self.call("organize_pdf", media_id=a_id, pages=[3, 1], rotate={"1": 90})
        merged = await self.call("merge_pdfs", media_ids=[organized["media_id"], b_id])
        signed = await self.call("sign_pdf", media_id=merged["media_id"], signer_name="Ada Lovelace")

        out = self.tmp / "signed.pdf"
        self.store.get_file(self.user, signed["media_id"], out)
        reader = PdfReader(out)
        self.assertEqual(len(reader.pages), 5)  # 2 + 2 + audit page
        self.assertIn("Ada Lovelace", reader.pages[3].extract_text())
        self.assertIn("audit trail", reader.pages[-1].extract_text())
        self.assertEqual(reader.metadata["/ArtCraftSignedBy"], "Ada Lovelace")
        self.assertEqual(len(signed["original_sha256"]), 64)

    async def test_rejects_missing_page(self):
        a = self.tmp / "a.pdf"
        make_pdf(a, 1)
        with self.assertRaisesRegex(ToolError, "does not exist"):
            await self.call("organize_pdf", media_id=self.add(a), pages=[2])


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg not installed")
class VideoTests(StudioTestCase):
    def make_clip(self, name, seconds=2):
        path = self.tmp / name
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc=size=320x240:rate=25:duration={seconds}",
             "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-shortest",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)],
            check=True,
        )
        return path

    async def test_trim_join_caption_and_extract(self):
        first = self.add(self.make_clip("one.mp4"))
        second = self.add(self.make_clip("two.mp4"))

        trimmed = await self.call("trim_video", media_id=first, start_seconds=0.5, end_seconds=1.5)
        joined = await self.call("join_videos", media_ids=[trimmed["media_id"], second], width=640, height=360)
        captioned = await self.call("add_text_to_video", media_id=joined["media_id"], text="It's \"ArtCraft\": 100%")
        audio = await self.call("extract_audio", media_id=captioned["media_id"])
        frame = await self.call("capture_frame", media_id=captioned["media_id"], at_seconds=1)

        info = await self.call("inspect_media", media_id=joined["media_id"])
        self.assertAlmostEqual(info["details"]["duration_seconds"], 3.0, delta=0.3)
        video_stream = next(s for s in info["details"]["streams"] if s["codec_type"] == "video")
        self.assertEqual((video_stream["width"], video_stream["height"]), (640, 360))
        self.assertTrue(audio["media_id"].endswith(".mp3"))
        self.assertTrue(frame["media_id"].endswith(".png"))

    async def test_transform_speed_and_resize(self):
        clip = self.add(self.make_clip("one.mp4"))
        result = await self.call("transform_video", media_id=clip, max_height=120, speed=2.0)
        info = await self.call("inspect_media", media_id=result["media_id"])
        self.assertAlmostEqual(info["details"]["duration_seconds"], 1.0, delta=0.2)

    def test_ffmpeg_refuses_playlists(self):
        playlist = self.tmp / "evil.mp4"
        playlist.write_text("#EXTM3U\n#EXTINF:1,\nfile:///etc/passwd\n")
        with self.assertRaises(engine.MediaError):
            engine.probe(playlist)


if __name__ == "__main__":
    unittest.main()
