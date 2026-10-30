"""End-to-end checks against the real Craft app CLIs.

Each test is skipped unless its app is installed under CRAFT_APPS_DIR, which is
the case inside the Docker image:

    docker run --rm -v "$PWD/tests:/app/tests:ro" artcraft-plugin \
        python -m unittest tests.test_craft_real -v
"""

import json
import os
import unittest

import craft_sessions
from tests.test_craft import CraftTestCase



def needs(name):
    app = craft_sessions.load_apps().get(name)
    return unittest.skipUnless(app and app.installed, f"{name}-cli is not installed")


class RealCraftAppTests(CraftTestCase):
    def setUp(self):
        super().setUp()
        # Undo CraftTestCase's fake app list; use the real manifest and install.
        for p in self.patches[-2:]:
            p.stop()
        del self.patches[-2:]

    async def assert_ok(self, session_id, tool, **arguments):
        result = await self.run_tool(session_id, tool, **arguments)
        self.assertFalse(result.is_error, f"{tool}: {self.text_of(result)}")
        return result

    async def assert_escapes_refused(self, session_id, tool, ext, **arguments):
        for path in (f"/tmp/escape.{ext}", f"../escape.{ext}"):
            refused = await self.run_tool(session_id, tool, path=path, **arguments)
            self.assertTrue(refused.is_error, f"{tool} wrote {path}")
            self.assertRegex(self.text_of(refused), "rejected|outside")
        self.assertFalse(os.path.exists(f"/tmp/escape.{ext}"))

    async def assert_saved(self, session_id, expected: dict):
        result = await self.call("save_craft_files", session_id=session_id)
        self.assertEqual({s["path"]: s["kind"] for s in result["saved"]}, expected, result["skipped"])
        return result

    async def open_session(self, app, hidden, media_ids=()):
        info = await self.call("open_craft_session", app=app, media_ids=list(media_ids))
        names = {t["name"] for t in info["tools"]}
        self.assertTrue(names.isdisjoint(hidden), names & set(hidden))
        return info

    @needs("wordcraft")
    async def test_write_a_letter_and_save_docx_and_pdf(self):
        notes = self.add_text("Meeting notes: ship hosted Craft.", ext="txt")
        info = await self.call("open_craft_session", app="wordcraft", media_ids=[notes])
        session_id = info["session_id"]
        tools = {t["name"] for t in info["tools"]}
        self.assertIn("save_document", tools)
        self.assertTrue(tools.isdisjoint({"screenshot", "click", "key", "ui_inspect"}))

        opened = await self.run_tool(session_id, "open_document", path=notes)
        self.assertFalse(opened.is_error, self.text_of(opened))
        self.assertIn("Meeting notes", self.text_of(await self.run_tool(session_id, "get_text")))

        await self.run_tool(session_id, "new_document", template="blank")
        await self.run_tool(session_id, "type_text", text="Dear team,\nHosted Craft works.")
        for name in ("letter.docx", "letter.pdf"):
            saved = await self.run_tool(session_id, "save_document", path=name)
            self.assertFalse(saved.is_error, self.text_of(saved))

        page = await self.run_tool(session_id, "render_page", page=1, scale=0.5)
        self.assertIn("image", [block.type for block in page.content])

        for escape in ("/tmp/escape.docx", "../escape.docx"):
            refused = await self.run_tool(session_id, "save_document", path=escape)
            self.assertTrue(refused.is_error)
            self.assertIn("automation path rejected", self.text_of(refused))
        self.assertFalse(os.path.exists("/tmp/escape.docx"))

        result = await self.call("save_craft_files", session_id=session_id)
        self.assertEqual(sorted(s["path"] for s in result["saved"]), ["letter.docx", "letter.pdf"])
        kinds = {s["path"]: s["kind"] for s in result["saved"]}
        self.assertEqual(kinds, {"letter.docx": "document", "letter.pdf": "pdf"})
        await self.call("close_craft_session", session_id=session_id)

    @needs("deckcraft")
    async def test_build_a_deck_and_export_pdf(self):
        info = await self.open_session("deckcraft", {"screenshot", "call_app"})
        sid = info["session_id"]
        await self.assert_ok(sid, "new_presentation", theme="Harbor")
        await self.assert_ok(sid, "add_slide", layout="titleAndContent", title="Hosted Craft", body="Works in Studio")
        slide = await self.assert_ok(sid, "render_slide", slide=1)
        self.assertIn("image", [block.type for block in slide.content])
        await self.assert_ok(sid, "save_presentation", path="deck.pptx")
        await self.assert_ok(sid, "export", path="deck.pdf", format="pdf")
        await self.assert_escapes_refused(sid, "save_presentation", "pptx")
        await self.assert_saved(sid, {"deck.pptx": "presentation", "deck.pdf": "pdf"})

    @needs("pdfcraft")
    async def test_create_combine_and_export_pdfs(self):
        hidden = {"printers", "printer_options", "doc_print", "sign_windows_ids", "sign_keychain_ids", "js_run"}
        info = await self.open_session("pdfcraft", hidden)
        sid = info["session_id"]
        self.assertFalse(info["schemas_included"], "PdfCraft's long tool list is sent compactly")
        details = await self.call("describe_craft_tools", session_id=sid, tools=["doc_create", "js_run"])
        self.assertEqual([t["name"] for t in details["tools"]], ["doc_create"])
        self.assertEqual(details["unknown"], ["js_run"])

        created = await self.assert_ok(sid, "doc_create", **{"from": "blank", "pages": 2})
        doc = json.loads(self.text_of(created))["doc"]
        await self.assert_ok(sid, "doc_save", doc=doc, path="a.pdf")
        await self.assert_ok(sid, "doc_save", doc=doc, path="b.pdf")
        await self.assert_ok(sid, "doc_combine", paths=["a.pdf", "b.pdf"], out="both.pdf")
        await self.assert_ok(sid, "doc_export_text", doc=doc, path="a.txt")
        await self.assert_escapes_refused(sid, "doc_save", "pdf", doc=doc)
        await self.assert_saved(sid, {"a.pdf": "pdf", "b.pdf": "pdf", "both.pdf": "pdf", "a.txt": "document"})

    @needs("photocraft")
    async def test_edit_an_uploaded_photo(self):
        from PIL import Image

        photo = self.tmp / "photo.png"
        Image.new("RGB", (80, 60), (200, 120, 40)).save(photo)
        media_id = self.add(photo)
        hidden = {"control_call", "ui_inspect", "ui_menu_invoke", "ui_pointer", "ui_screenshot", "ui_set"}
        sid = (await self.open_session("photocraft", hidden, [media_id]))["session_id"]
        await self.assert_ok(sid, "doc_open", path=media_id)
        preview = await self.assert_ok(sid, "render_preview", max_side=64)
        self.assertIn("image", [block.type for block in preview.content])
        await self.assert_ok(sid, "doc_save", path="edit.pcraft")
        await self.assert_ok(sid, "doc_export", path="edit.jpg", quality=85)
        await self.assert_escapes_refused(sid, "doc_export", "png")
        await self.assert_saved(sid, {"edit.pcraft": "project", "edit.jpg": "image"})

    @needs("vectorcraft")
    async def test_draw_vector_art_headless(self):
        hidden = {"ui_inspect", "ui_screenshot", "inspect_ui", "type_text", "answer_dialog", "open_panel"}
        sid = (await self.open_session("vectorcraft", hidden))["session_id"]
        await self.assert_ok(sid, "draw_shape", shape="ellipse", x=20, y=20, width=200, height=120, fill="#3366ff")
        await self.assert_ok(sid, "add_text", x=40, y=200, text="Hosted Craft")
        await self.assert_ok(sid, "save_file", path="art.vectorcraft")
        await self.assert_ok(sid, "export", path="art.pdf", format="pdf")
        await self.assert_ok(sid, "export", path="art.svg", format="svg")
        await self.assert_escapes_refused(sid, "save_file", "vectorcraft")
        result = await self.assert_saved(sid, {"art.vectorcraft": "project", "art.pdf": "pdf"})
        self.assertEqual([s["path"] for s in result["skipped"]], ["art.svg"], "SVG is not stored (XSS risk)")

    async def assert_blocked(self, session_id, tool, **arguments):
        refused = await self.run_tool(session_id, tool, **arguments)
        self.assertTrue(refused.is_error)
        self.assertIn("turned off in Studio", self.text_of(refused))

    @needs("designcraft")
    async def test_lay_out_a_page_with_an_uploaded_image(self):
        from PIL import Image

        photo = self.tmp / "photo.png"
        Image.new("RGB", (120, 80), (40, 120, 200)).save(photo)
        media_id = self.add(photo)
        hidden = {"screenshot", "click", "drag", "menu_list", "ui_inspect", "ui_set", "dialog_set", "dialog_confirm", "dialog_cancel"}
        sid = (await self.open_session("designcraft", hidden, [media_id]))["session_id"]
        await self.assert_ok(sid, "new_document", pages=1)
        await self.assert_ok(sid, "place_image", path=media_id)
        page = await self.assert_ok(sid, "render_page", page=0, scale=0.25)
        self.assertIn("image", [block.type for block in page.content])
        await self.assert_ok(sid, "save_document", path="flyer.designcraft")
        await self.assert_ok(sid, "export_png", path="flyer.png", page=0, scale=0.25)
        await self.assert_ok(sid, "execute", command="file.exportPdf", params={"path": "flyer.pdf"})
        await self.assert_escapes_refused(sid, "save_document", "designcraft")
        await self.assert_saved(sid, {"flyer.designcraft": "project", "flyer.png": "image", "flyer.pdf": "pdf"})

    @needs("figcraft")
    async def test_design_a_screen_and_export_it(self):
        sid = (await self.open_session("figcraft", set()))["session_id"]
        await self.assert_ok(sid, "create_node", type="frame", x=0, y=0, width=320, height=200, name="Card")
        await self.assert_ok(sid, "create_node", type="text", x=16, y=16, text="Hosted Craft")
        shot = await self.assert_ok(sid, "screenshot")
        self.assertIn("image", [block.type for block in shot.content])
        await self.assert_ok(sid, "save_file", path="card.figcraft")
        await self.assert_ok(sid, "export", path="card.png", format="png")
        await self.assert_escapes_refused(sid, "save_file", "figcraft")
        await self.assert_blocked(sid, "run_command", command="collab.host", params={"bind": "lan"})
        await self.assert_blocked(sid, "batch", commands=[{"command": "plugin.run", "params": {}}])
        await self.assert_saved(sid, {"card.figcraft": "project", "card.png": "image"})

    @needs("soundcraft")
    async def test_mix_an_uploaded_recording(self):
        import wave

        recording = self.tmp / "take.wav"
        with wave.open(str(recording), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000); w.writeframes(b"\1\0" * 8000)
        media_id = self.add(recording)
        hidden = {"screenshot", "ui_inspect", "ui_set", "menu_invoke", "click", "drag", "key", "parity"}
        sid = (await self.open_session("soundcraft", hidden, [media_id]))["session_id"]
        await self.assert_ok(sid, "new_session", name="Demo")
        await self.assert_ok(sid, "import_audio", path=media_id)
        await self.assert_ok(sid, "bounce_mix", path="mix.wav", format="wav")
        await self.assert_ok(sid, "save_session", path="demo.scraft")
        await self.assert_escapes_refused(sid, "save_session", "scraft")
        await self.assert_blocked(sid, "execute", command="setup.disk_allocation", params={"path": "/tmp"})
        result = await self.call("save_craft_files", session_id=sid)
        saved = {s["path"]: s["kind"] for s in result["saved"]}
        self.assertEqual(saved.get("mix.wav"), "audio", result)
        self.assertEqual(saved.get("demo.scraft"), "project", result)
