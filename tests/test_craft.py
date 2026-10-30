import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError

import craft_sessions
from craft_sessions import CraftApp
from studio_mcp import studio
from tests.test_studio import StudioTestCase

FAKE_CLI = Path(__file__).with_name("fake_craft_cli.py").resolve()


class CraftTestCase(StudioTestCase):
    def setUp(self):
        super().setUp()
        apps_dir = self.tmp / "craft"
        cli = apps_dir / "fakecraft" / "fakecraft-cli"
        cli.parent.mkdir(parents=True)
        cli.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_CLI}" "$@"\n')
        cli.chmod(0o755)
        self.app = CraftApp(
            name="fakecraft", title="FakeCraft", version="0.0.1", description="Test app.",
            website="https://example.com", extensions=("txt", "docx", "pdf"),
            hidden_tools=frozenset({"screenshot"}), mcp_args=("--headless",),
            blocked_commands=("collab.", "net.fetch"),
        )
        missing = CraftApp(
            name="missingcraft", title="MissingCraft", version="1", description="Not installed.",
            website="https://example.com", extensions=("txt",), hidden_tools=frozenset(),
        )
        for p in (
            patch.dict(os.environ, {"CRAFT_APPS_DIR": str(apps_dir)}),
            patch("craft_sessions.load_apps", return_value={"fakecraft": self.app, "missingcraft": missing}),
        ):
            p.start()
            self.patches.append(p)

    async def asyncTearDown(self):
        await craft_sessions.manager.close_all()

    def add_text(self, text="hello", ext="txt") -> str:
        path = self.tmp / f"input.{ext}"
        path.write_text(text)
        return self.add(path)

    async def run_tool(self, session_id, tool, **arguments):
        async with Client(studio) as client:
            return await client.call_tool(
                "run_craft_tool", {"session_id": session_id, "tool": tool, "arguments": arguments})

    @staticmethod
    def text_of(result) -> str:
        return " ".join(block.text for block in result.content if block.type == "text")


class CraftAppListTests(CraftTestCase):
    async def test_lists_only_installed_apps(self):
        apps = (await self.call("list_craft_apps"))["apps"]
        self.assertEqual([a["name"] for a in apps], ["fakecraft"])
        self.assertEqual(apps[0]["file_types"], ["txt", "docx", "pdf"])

    async def test_unknown_or_missing_app_is_refused(self):
        for name in ("nope", "missingcraft"):
            with self.assertRaisesRegex(ToolError, "not available. Available apps: fakecraft"):
                await self.call("open_craft_session", app=name)

    async def test_requires_sign_in(self):
        with patch.dict(os.environ, {"STUDIO_DEV_USER": ""}):
            with self.assertRaisesRegex(ToolError, "Sign in"):
                await self.call("open_craft_session", app="fakecraft")


class CraftSessionTests(CraftTestCase):
    async def test_open_edit_save_and_close(self):
        media_id = self.add_text("original text")
        info = await self.call("open_craft_session", app="fakecraft", media_ids=[media_id])
        session_id = info["session_id"]
        self.assertTrue(session_id.startswith("craft_"))
        self.assertEqual(info["files"], [media_id])
        self.assertNotIn("screenshot", [t["name"] for t in info["tools"]])

        read = await self.run_tool(session_id, "read_file", path=media_id)
        self.assertFalse(read.is_error)
        self.assertEqual(self.text_of(read), "original text")
        wrote = await self.run_tool(session_id, "write_file", path="report.docx", text="new document")
        self.assertFalse(wrote.is_error)

        result = await self.call("save_craft_files", session_id=session_id)
        self.assertEqual([s["path"] for s in result["saved"]], ["report.docx"])
        saved = result["saved"][0]
        self.assertEqual(saved["kind"], "document")
        copy = self.tmp / "copy.docx"
        self.store.get_file(self.user, saved["media_id"], copy)
        self.assertEqual(copy.read_text(), "new document")

        again = await self.call("save_craft_files", session_id=session_id)
        self.assertEqual(again["saved"], [], "unchanged files are not saved twice")

        session = craft_sessions.manager.sessions[session_id]
        folder, process = Path(session.folder.name), session.client.process
        closed = await self.call("close_craft_session", session_id=session_id)
        self.assertEqual(closed["result"], f"Closed {session_id}.")
        self.assertFalse(folder.exists())
        self.assertIsNotNone(process.returncode)

    async def test_overwriting_an_input_saves_a_new_file_and_keeps_the_original(self):
        media_id = self.add_text("v1")
        session_id = (await self.call("open_craft_session", app="fakecraft", media_ids=[media_id]))["session_id"]
        await self.run_tool(session_id, "write_file", path=media_id, text="v2")
        saved = (await self.call("save_craft_files", session_id=session_id))["saved"]
        self.assertEqual(len(saved), 1)
        self.assertNotEqual(saved[0]["media_id"], media_id)
        original = self.tmp / "orig.txt"
        self.store.get_file(self.user, media_id, original)
        self.assertEqual(original.read_text(), "v1")

    async def test_app_runs_inside_its_folder_and_refuses_escapes(self):
        session_id = (await self.call("open_craft_session", app="fakecraft"))["session_id"]
        root = craft_sessions.manager.sessions[session_id].root.resolve()
        self.assertEqual(Path(self.text_of(await self.run_tool(session_id, "cwd"))).resolve(), root)
        escape = await self.run_tool(session_id, "write_file", path="../escape.txt", text="x")
        self.assertTrue(escape.is_error)
        self.assertIn("outside the write root", self.text_of(escape))
        self.assertFalse((root.parent / "escape.txt").exists())

    async def test_app_never_sees_server_secrets(self):
        with patch.dict(os.environ, {"S3_SECRET_ACCESS_KEY": "topsecret", "OAUTH_AUDIENCE": "aud"}):
            session_id = (await self.call("open_craft_session", app="fakecraft"))["session_id"]
            env = self.text_of(await self.run_tool(session_id, "env"))
        self.assertNotIn("topsecret", env)
        self.assertNotIn("OAUTH_AUDIENCE", env)
        session = craft_sessions.manager.sessions[session_id]
        self.assertIn(f'"HOME": "{session.home}"', env)

    async def test_hidden_and_unknown_tools_are_refused(self):
        session_id = (await self.call("open_craft_session", app="fakecraft"))["session_id"]
        for tool in ("screenshot", "rm_rf"):
            result = await self.run_tool(session_id, tool)
            self.assertTrue(result.is_error)
            self.assertIn("has no tool", self.text_of(result))

    async def test_blocked_engine_commands_never_reach_the_app(self):
        session_id = (await self.call("open_craft_session", app="fakecraft"))["session_id"]
        root = craft_sessions.manager.sessions[session_id].root
        for arguments in (
            {"path": "a.txt", "text": "collab.host"},
            {"path": "a.txt", "steps": [{"command": "rect"}, {"command": "net.fetch", "params": {}}]},
            {"path": "a.txt", "text": '{"command": "collab\\u002ehost"}'},
        ):
            result = await self.run_tool(session_id, "write_file", **arguments)
            self.assertTrue(result.is_error, arguments)
            self.assertIn("turned off in Studio", self.text_of(result))
        self.assertFalse((root / "a.txt").exists(), "a blocked call is refused before the app sees it")

        # Ordinary text that only mentions the words is fine.
        allowed = await self.run_tool(session_id, "write_file", path="a.txt", text="collab notes; net.fetcher; see collab.")
        self.assertFalse(allowed.is_error, self.text_of(allowed))

    async def test_wrong_file_type_is_refused(self):
        image = self.tmp / "x.png"
        image.write_bytes(b"\x89PNG")
        media_id = self.add(image)
        with self.assertRaisesRegex(ToolError, "cannot open .png"):
            await self.call("open_craft_session", app="fakecraft", media_ids=[media_id])
        self.assertEqual(craft_sessions.manager.sessions, {}, "a failed open leaves no session behind")

    async def test_add_files_to_open_session(self):
        session_id = (await self.call("open_craft_session", app="fakecraft"))["session_id"]
        media_id = self.add_text("extra")
        files = (await self.call("add_craft_files", session_id=session_id, media_ids=[media_id]))["files"]
        self.assertEqual(files, [media_id])
        self.assertEqual((await self.call("save_craft_files", session_id=session_id))["saved"], [],
                         "added inputs are not saved back as results")

    async def test_save_skips_symlinks_and_unsupported_types(self):
        session_id = (await self.call("open_craft_session", app="fakecraft"))["session_id"]
        root = craft_sessions.manager.sessions[session_id].root
        secret = self.tmp / "secret.txt"
        secret.write_text("other user's data")
        (root / "link.txt").symlink_to(secret)
        await self.run_tool(session_id, "write_file", path="tool.exe", text="x")
        await self.run_tool(session_id, "write_file", path="ok.txt", text="fine")
        result = await self.call("save_craft_files", session_id=session_id)
        self.assertEqual([s["path"] for s in result["saved"]], ["ok.txt"])
        self.assertEqual([s["path"] for s in result["skipped"]], ["tool.exe"])
        self.assertIn("Unsupported file type", result["skipped"][0]["reason"])

    async def test_app_is_started_with_its_manifest_options(self):
        session_id = (await self.call("open_craft_session", app="fakecraft"))["session_id"]
        root = str(craft_sessions.manager.sessions[session_id].root)
        argv = self.text_of(await self.run_tool(session_id, "argv"))
        self.assertEqual(argv, f'["mcp", "--headless", "--automation-read-root", "{root}", '
                               f'"--automation-write-root", "{root}"]')

    async def test_small_tool_lists_include_schemas(self):
        info = await self.call("open_craft_session", app="fakecraft")
        self.assertTrue(info["schemas_included"])
        argv = next(t for t in info["tools"] if t["name"] == "argv")
        self.assertIn("verbose", argv["input_schema"]["properties"])

    async def test_large_tool_lists_are_compact_with_details_on_request(self):
        with patch("studio_mcp.MAX_INLINE_TOOL_SCHEMAS", 3):
            info = await self.call("open_craft_session", app="fakecraft")
        self.assertFalse(info["schemas_included"])
        argv = next(t for t in info["tools"] if t["name"] == "argv")
        self.assertIsNone(argv["input_schema"])
        self.assertLessEqual(len(argv["description"]), 120)
        details = await self.call("describe_craft_tools", session_id=info["session_id"],
                                  tools=["argv", "screenshot", "nope"])
        self.assertEqual([t["name"] for t in details["tools"]], ["argv"])
        self.assertIn("verbose", details["tools"][0]["input_schema"]["properties"])
        self.assertGreater(len(details["tools"][0]["description"]), 120)
        self.assertEqual(details["unknown"], ["screenshot", "nope"], "hidden tools stay hidden")

    async def test_images_pass_through_and_long_text_is_truncated(self):
        session_id = (await self.call("open_craft_session", app="fakecraft"))["session_id"]
        image = await self.run_tool(session_id, "image")
        self.assertEqual(image.content[0].type, "image")
        self.assertEqual(image.content[0].mime_type, "image/png")
        big = self.text_of(await self.run_tool(session_id, "big_text"))
        self.assertLess(len(big), 101_000)
        self.assertTrue(big.endswith("[... output truncated by Studio]"))


class CraftIsolationAndLimitTests(CraftTestCase):
    async def test_other_users_cannot_use_a_session(self):
        session_id = (await self.call("open_craft_session", app="fakecraft"))["session_id"]
        with patch.dict(os.environ, {"STUDIO_DEV_USER": "bob"}):
            result = await self.run_tool(session_id, "cwd")
            self.assertTrue(result.is_error)
            self.assertIn("was not found", self.text_of(result))
            with self.assertRaisesRegex(ToolError, "was not found"):
                await self.call("describe_craft_tools", session_id=session_id, tools=["cwd"])
            for tool in ("save_craft_files", "close_craft_session"):
                with self.assertRaisesRegex(ToolError, "was not found"):
                    await self.call(tool, session_id=session_id)
        self.assertIn(session_id, craft_sessions.manager.sessions)

    async def test_per_user_session_limit(self):
        with patch("craft_sessions.MAX_SESSIONS_PER_USER", 2):
            first = (await self.call("open_craft_session", app="fakecraft"))["session_id"]
            await self.call("open_craft_session", app="fakecraft")
            with self.assertRaisesRegex(ToolError, f"already have 2 open Craft sessions .*{first}"):
                await self.call("open_craft_session", app="fakecraft")
            with patch.dict(os.environ, {"STUDIO_DEV_USER": "bob"}):
                await self.call("open_craft_session", app="fakecraft")

    async def test_server_wide_session_limit(self):
        with patch("craft_sessions.MAX_SESSIONS", 1):
            await self.call("open_craft_session", app="fakecraft")
            with patch.dict(os.environ, {"STUDIO_DEV_USER": "bob"}):
                with self.assertRaisesRegex(ToolError, "as many Craft sessions as it can"):
                    await self.call("open_craft_session", app="fakecraft")

    async def test_slow_tool_times_out_and_closes_the_session(self):
        session_id = (await self.call("open_craft_session", app="fakecraft"))["session_id"]
        with patch("craft_sessions.CALL_TIMEOUT_SECONDS", 1):
            started = time.monotonic()
            result = await self.run_tool(session_id, "sleep", seconds=30)
        self.assertLess(time.monotonic() - started, 10)
        self.assertTrue(result.is_error)
        self.assertIn("took longer than 1 seconds", self.text_of(result))
        self.assertNotIn(session_id, craft_sessions.manager.sessions)

    async def test_crashed_app_closes_the_session(self):
        session_id = (await self.call("open_craft_session", app="fakecraft"))["session_id"]
        result = await self.run_tool(session_id, "crash")
        self.assertTrue(result.is_error)
        self.assertIn("stopped unexpectedly", self.text_of(result))
        self.assertNotIn(session_id, craft_sessions.manager.sessions)

    async def test_idle_sessions_are_closed(self):
        session_id = (await self.call("open_craft_session", app="fakecraft"))["session_id"]
        folder = Path(craft_sessions.manager.sessions[session_id].folder.name)
        with patch("craft_sessions.IDLE_SECONDS", 0):
            await self.call("list_craft_apps")
        self.assertNotIn(session_id, craft_sessions.manager.sessions)
        self.assertFalse(folder.exists())


class CraftManifestTests(CraftTestCase):
    def test_manifest_pins_checksums_for_both_architectures(self):
        import json

        manifest = json.loads(craft_sessions.MANIFEST_PATH.read_text())
        self.assertTrue(manifest["apps"])
        for app in manifest["apps"]:
            self.assertEqual(set(app["sha256"]), {"x86_64", "aarch64"})
            for digest in app["sha256"].values():
                self.assertRegex(digest, r"^[0-9a-f]{64}$")
            self.assertTrue(app["confinement_checked"])

    def test_every_manifest_file_type_can_be_stored(self):
        import json

        from media_storage import new_media_id, validate_media_id

        for app in json.loads(craft_sessions.MANIFEST_PATH.read_text())["apps"]:
            for ext in app["extensions"]:
                validate_media_id(new_media_id(ext))  # raises if the type is unknown or too long
