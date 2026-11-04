# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Adithya Shankaran

import asyncio
import io
import json
import os
import shutil
import tempfile
import unittest
import wave
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image
from pypdf import PdfWriter

import craft_sessions
import eval_results
from evals import checks, run_evals
from media_storage import LocalStorage


class TempCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)


class CheckTests(TempCase):
    def fails(self, check, *args, **kwargs):
        with self.assertRaises(checks.CheckFailed):
            check(*args, **kwargs)

    def test_pdf(self):
        path = self.tmp / "a.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer.write(path)
        checks.pdf(path, pages=1)
        self.fails(checks.pdf, path, pages=2)
        self.fails(checks.pdf, path, text="missing words")
        path.write_bytes(b"not a pdf")
        self.fails(checks.pdf, path)

    def test_office_text(self):
        path = self.tmp / "a.docx"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("word/document.xml", "<w:t>Hosted </w:t><w:t>Craft</w:t>")
        checks.office_text(path, r"word/document\.xml", "Hosted Craft")
        self.fails(checks.office_text, path, r"word/document\.xml", "Something else")
        self.fails(checks.office_text, path, r"ppt/slides/slide\d+\.xml", "Hosted")
        path.write_bytes(b"not a zip")
        self.fails(checks.office_text, path, r"word/document\.xml", "Hosted")

    def test_images(self):
        flat = self.tmp / "flat.png"
        Image.new("RGB", (40, 20), (200, 120, 40)).save(flat)
        checks.image_colour(flat, "PNG", (200, 120, 40), size=(40, 20))
        checks.image_has_colour(flat, "PNG", (200, 120, 40))
        self.fails(checks.image, flat, "PNG")
        self.fails(checks.image_colour, flat, "PNG", (40, 120, 200))
        self.fails(checks.image_colour, flat, "PNG", (200, 120, 40), size=(10, 10))
        self.fails(checks.image_colour, flat, "JPEG", (200, 120, 40))
        self.fails(checks.image_has_colour, flat, "PNG", (40, 120, 200))
        mixed = self.tmp / "mixed.png"
        picture = Image.new("RGB", (40, 20), (255, 255, 255))
        picture.paste((40, 120, 200), (0, 0, 10, 10))
        picture.save(mixed)
        checks.image(mixed, "PNG")
        checks.image_has_colour(mixed, "PNG", (40, 120, 200))
        flat.write_bytes(b"not an image")
        self.fails(checks.image, flat, "PNG")

    def write_wav(self, path, sample: bytes, frames=8000):
        with wave.open(str(path), "wb") as sound:
            sound.setnchannels(1)
            sound.setsampwidth(len(sample))
            sound.setframerate(8000)
            sound.writeframes(sample * frames)

    def test_audio(self):
        path = self.tmp / "a.wav"
        self.write_wav(path, b"\x00\x40")
        checks.audio(path, seconds=1)
        self.fails(checks.audio, path, seconds=3)
        self.write_wav(path, b"\x00\x00\x40")
        checks.audio(path, seconds=1)
        self.write_wav(path, b"\x01\x00")
        self.fails(checks.audio, path, seconds=1)  # next to silent
        self.write_wav(path, b"\x00\x00\x00\x00")
        self.fails(checks.audio, path, seconds=1)
        path.write_bytes(b"not a wav")
        self.fails(checks.audio, path, seconds=1)

    def test_not_empty(self):
        path = self.tmp / "a.bin"
        path.write_bytes(b"")
        self.fails(checks.not_empty, path)
        path.write_bytes(b"x")
        checks.not_empty(path)


def fake_app(name="fakecraft", installed=True):
    return SimpleNamespace(name=name, title="FakeCraft", version="1.0", installed=installed)


class RunnerTests(TempCase):
    def setUp(self):
        super().setUp()
        self.store = LocalStorage(self.tmp / "store")

    def run_with(self, job, app=None):
        return asyncio.run(run_evals.run_all([app or fake_app()], self.store, {"fakecraft": job}))

    def test_every_hosted_app_has_a_job(self):
        self.assertEqual(set(run_evals.JOBS), set(craft_sessions.load_apps()))

    def test_a_good_job_passes(self):
        async def job(run):
            run.record("a check", True)

        report = self.run_with(job)
        self.assertTrue(report["passed"])
        self.assertEqual(report["apps"][0]["checks"], [{"name": "a check", "passed": True}])
        json.dumps(report)

    def test_a_failed_check_fails_the_report_and_the_job_carries_on(self):
        async def job(run):
            target = run.folder / "out.pdf"
            target.write_bytes(b"not a pdf")
            run.saved["out.pdf"] = target
            run.check("the PDF opens", checks.pdf, "out.pdf")
            run.check("the other file exists", checks.not_empty, "missing.png")
            run.record("a later check", True)

        report = self.run_with(job)
        self.assertFalse(report["passed"])
        rows = report["apps"][0]["checks"]
        self.assertEqual([row["passed"] for row in rows], [False, False, True])
        self.assertIn("out.pdf not a readable PDF", rows[0]["detail"])
        self.assertIn("missing.png wasn't saved", rows[1]["detail"])

    def test_failures_and_crashes_are_reported_for_each_app(self):
        async def fails(run):
            raise run_evals.JobFailed("new_document: refused")

        async def crashes(run):
            raise RuntimeError("secret detail")

        self.assertIn("refused", self.run_with(fails)["apps"][0]["checks"][0]["detail"])
        with patch("traceback.print_exc"):
            crashed = self.run_with(crashes)
        self.assertFalse(crashed["passed"])
        self.assertEqual(crashed["apps"][0]["checks"][0]["detail"], "RuntimeError")

    def test_a_job_that_records_nothing_does_not_pass(self):
        async def job(run):
            pass

        self.assertFalse(self.run_with(job)["passed"])

    def test_missing_app_or_job_fails(self):
        async def job(run):
            run.record("a check", True)

        self.assertFalse(self.run_with(job, fake_app(installed=False))["passed"])
        self.assertFalse(self.run_with(job, fake_app(name="otherapp"))["passed"])
        self.assertFalse(asyncio.run(run_evals.run_all([], self.store))["passed"])

    def test_long_details_are_cut(self):
        async def job(run):
            raise run_evals.JobFailed("x" * 5000)

        self.assertEqual(len(self.run_with(job)["apps"][0]["checks"][0]["detail"]), run_evals.MAX_DETAIL)


def report(**changes):
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    base = {
        "schema": 1, "generated_at": now, "commit": "abc1234", "passed": False,
        "apps": [
            {"app": "wordcraft", "title": "WordCraft", "version": "0.12.0", "passed": True, "ms": 98,
             "checks": [{"name": "opens", "passed": True}]},
            {"app": "soundcraft", "title": "SoundCraft", "version": "0.14.0", "passed": False, "ms": 40, "secret": "dropped",
             "checks": [{"name": "opens", "passed": True},
                        {"name": "the mix isn't silent", "passed": False, "detail": "<b>mix.wav</b> is nearly silent"}]},
        ],
    }
    return {**base, **changes}


class ResultsTests(unittest.TestCase):
    def setUp(self):
        eval_results._cache.clear()
        eval_results._thread.clear()
        self.addCleanup(eval_results._cache.clear)

    def test_parse_keeps_only_what_the_page_uses(self):
        parsed = eval_results.parse(report())
        self.assertFalse(parsed["passed"])
        self.assertEqual(parsed["apps_passed"], 1)
        self.assertFalse(parsed["stale"])
        self.assertEqual(parsed["apps"][1]["checks_passed"], 1)
        self.assertEqual(parsed["apps"][1]["failures"][0]["name"], "the mix isn't silent")
        self.assertNotIn("secret", json.dumps(parsed))

    def test_a_report_claiming_a_pass_with_a_failed_app_is_not_a_pass(self):
        self.assertFalse(eval_results.parse(report(passed=True))["passed"])

    def test_old_reports_are_marked(self):
        old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        self.assertTrue(eval_results.parse(report(generated_at=old))["stale"])

    def test_unreadable_reports_are_refused(self):
        for bad in ({}, report(schema=2), report(apps=[]), report(apps="x"), report(generated_at="soon"), report(apps=[{"app": "x"}])):
            self.assertIsNone(eval_results.parse(bad), bad)

    def test_local_copies_do_not_fetch(self):
        with patch.dict(os.environ, {"RENDER": "", "EVAL_RESULTS_URL": ""}), patch("eval_results.request.urlopen") as urlopen:
            self.assertIsNone(eval_results.latest())
        urlopen.assert_not_called()

    def response(self, body: bytes):
        response = io.BytesIO(body)
        return patch("eval_results.request.urlopen", return_value=response)

    def test_fetches_once_and_keeps_the_last_good_report(self):
        with patch.dict(os.environ, {"EVAL_RESULTS_URL": "https://example.test/latest.json"}):
            with self.response(json.dumps(report()).encode()) as urlopen:
                self.assertEqual(eval_results.latest()["commit"], "abc1234")
                eval_results.latest()
            self.assertEqual(urlopen.call_count, 1)
            # The cache runs out and the next fetch fails or is junk.
            for failing in (patch("eval_results.request.urlopen", side_effect=OSError("down")), self.response(b"not json")):
                eval_results._cache["saved"] = (-10**9, eval_results._cache["saved"][1])
                with failing, self.assertLogs("bridgecraft.evals", "WARNING"):
                    self.assertEqual(eval_results.latest()["commit"], "abc1234")

    def test_status_page_shows_the_report(self):
        from app import app

        with patch("eval_results.latest", return_value=eval_results.parse(report())):
            page = app.test_client().get("/status").get_data(as_text=True)
        self.assertIn('id="status-evals"', page)
        self.assertIn("1 of 2 apps passed", page)
        self.assertIn("SoundCraft", page)
        self.assertIn("1 of 2</td>", page)
        # Report text is escaped.
        self.assertIn("&lt;b&gt;mix.wav&lt;/b&gt;", page)
        self.assertNotIn("<b>mix.wav", page)
        self.assertNotIn("data-eval-stale", page)

    def test_status_page_with_no_or_old_report(self):
        from app import app

        with patch("eval_results.latest", return_value=None):
            page = app.test_client().get("/status").get_data(as_text=True)
        self.assertIn("No results to show yet", page)
        old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        with patch("eval_results.latest", return_value=eval_results.parse(report(generated_at=old))):
            page = app.test_client().get("/status").get_data(as_text=True)
        self.assertIn("data-eval-stale", page)


if __name__ == "__main__":
    unittest.main()
