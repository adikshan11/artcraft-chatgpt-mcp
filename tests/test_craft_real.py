"""End-to-end checks against the real WordCraft CLI.

Skipped unless wordcraft-cli is installed under CRAFT_APPS_DIR, which is the
case inside the Docker image:

    docker run --rm -v "$PWD/tests:/app/tests:ro" artcraft-plugin \
        python -m unittest tests.test_craft_real -v
"""

import os
import unittest

import craft_sessions
from tests.test_craft import CraftTestCase

WORDCRAFT = craft_sessions.load_apps().get("wordcraft")


@unittest.skipUnless(WORDCRAFT and WORDCRAFT.installed, "wordcraft-cli is not installed")
class RealWordCraftTests(CraftTestCase):
    def setUp(self):
        super().setUp()
        # Undo CraftTestCase's fake app list; use the real manifest and install.
        for p in self.patches[-2:]:
            p.stop()
        del self.patches[-2:]

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
