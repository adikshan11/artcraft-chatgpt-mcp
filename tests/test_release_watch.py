# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Adithya Shankaran

import importlib.util
import json
import os
import re
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import release_watch
import site_status

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 11, 4, tzinfo=timezone.utc)


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def release_info(**versions):
    return {"release_info": {"apps": {
        name: {"latest_release": {"version": version, "published_at": "2026-11-03T00:00:00Z"}}
        for name, version in versions.items()
    }}}


class ReleaseListTests(unittest.TestCase):
    def setUp(self):
        for store in (release_watch._cache, release_watch._thread):
            store.clear()
            self.addCleanup(store.clear)

    def test_parse_drops_malformed_versions(self):
        data = release_info(wordcraft="0.15.1", deckcraft="../../etc", pdfcraft="1.0-rc1")
        data["release_info"]["apps"]["photocraft"] = {"latest_release": None}
        self.assertEqual(list(release_watch.parse_release_info(data)), ["wordcraft"])

    def test_versions_compare_as_numbers(self):
        behind = release_watch.compare({"a": "0.9.0"}, {"a": {"version": "0.10.0", "published_at": None}}, NOW)
        self.assertEqual(behind["behind"][0]["latest"], "0.10.0")
        ahead = release_watch.compare({"a": "0.10.0"}, {"a": {"version": "0.9.0", "published_at": None}}, NOW)
        self.assertEqual(ahead["current"], 1)

    def test_a_new_release_is_overdue_only_after_the_grace_period(self):
        for age, overdue in ((timedelta(days=1), False), (timedelta(days=8), True)):
            published = (NOW - age).isoformat()
            result = release_watch.compare({"a": "1.0"}, {"a": {"version": "1.1", "published_at": published}}, NOW)
            self.assertEqual(result["overdue"], overdue)

    def test_an_app_missing_from_the_list_asks_github(self):
        def fake(url, headers=None):
            if "storyteller" in url:
                return release_info(wordcraft="0.15.1")
            self.assertIn("storytold/texturecraft", url)
            return {"tag_name": "v0.3.0", "published_at": "2026-11-03T00:00:00Z"}

        with patch.object(release_watch, "_get_json", fake):
            found = release_watch.fetch_latest(["wordcraft", "texturecraft"])
        self.assertEqual(found["texturecraft"]["version"], "0.3.0")

    def test_a_rate_limited_api_falls_back_to_the_release_page(self):
        def fake(url, headers=None):
            if "storyteller" in url:
                return release_info(wordcraft="0.15.1")
            raise OSError("rate limited")

        redirected = MagicMock()
        redirected.__enter__.return_value.geturl.return_value = "https://github.com/storytold/figcraft/releases/tag/v0.15.0"
        with patch.object(release_watch, "_get_json", fake), patch.object(release_watch.request, "urlopen", return_value=redirected):
            found = release_watch.fetch_latest(["figcraft"])
        self.assertEqual(found["figcraft"], {"version": "0.15.0", "published_at": None})

    def test_a_release_page_with_no_tag_gives_no_answer(self):
        def fake(url, headers=None):
            if "storyteller" in url:
                return release_info(wordcraft="0.15.1")
            raise OSError("rate limited")

        # With no release, GitHub stays on the /releases page.
        stayed = MagicMock()
        stayed.__enter__.return_value.geturl.return_value = "https://github.com/storytold/figcraft/releases"
        with patch.object(release_watch, "_get_json", fake), patch.object(release_watch.request, "urlopen", return_value=stayed):
            self.assertEqual(list(release_watch.fetch_latest(["wordcraft", "figcraft"])), ["wordcraft"])

    def test_an_app_with_no_answer_keeps_its_last_one(self):
        release_watch._cache["latest"] = {"figcraft": {"version": "0.15.0", "published_at": None}}
        with patch.object(release_watch, "fetch_latest", return_value={"wordcraft": {"version": "0.15.1", "published_at": None}}):
            release_watch._fetch(["wordcraft", "figcraft"])
        self.assertEqual(sorted(release_watch._cache["latest"]), ["figcraft", "wordcraft"])

    def test_a_failed_fetch_keeps_the_last_answer_and_backs_off(self):
        names = ["wordcraft"]
        calls = []

        def good(url, headers=None):
            calls.append(url)
            return release_info(wordcraft="0.15.1")

        def bad(url, headers=None):
            calls.append(url)
            raise OSError("down")

        with patch.dict(os.environ, {"RELEASE_WATCH": "1"}):
            with patch.object(release_watch, "_get_json", good):
                first = release_watch.latest(names)
            release_watch._cache["next_at"] = 0
            with patch.object(release_watch, "_get_json", bad):
                second = release_watch.latest(names)
                third = release_watch.latest(names)
        self.assertEqual(first["latest"], second["latest"])
        self.assertEqual(second["latest"], third["latest"])
        # The second read failed; the third didn't try again.
        self.assertEqual(len(calls), 2)

    def test_garbage_from_upstream_is_a_failed_fetch(self):
        with patch.dict(os.environ, {"RELEASE_WATCH": "1"}), patch.object(release_watch, "_get_json", lambda *a, **k: {"nope": 1}):
            self.assertIsNone(release_watch.latest(["wordcraft"]))

    def test_off_on_a_local_copy(self):
        with patch.dict(os.environ, clear=True), patch.object(release_watch, "_get_json", side_effect=AssertionError):
            self.assertIsNone(release_watch.latest(["wordcraft"]))


class StatusRowTests(unittest.TestCase):
    def rows(self, feed, evals=None):
        with patch.object(release_watch, "latest", return_value=feed), patch.dict(os.environ, {"RENDER": "1"}):
            rows, versions = site_status.freshness(evals)
        return {row["name"]: row for row in rows}, versions

    def pins(self):
        return {app["name"]: app["version"] for app in json.loads((ROOT / "craft_apps.json").read_text())["apps"]}

    def feed(self, fetched=None, **overrides):
        latest = {name: {"version": version, "published_at": "2026-11-03T00:00:00Z"} for name, version in self.pins().items()}
        latest.update(overrides)
        return {"latest": latest, "fetched_at": fetched or datetime.now(timezone.utc)}

    def test_everything_current(self):
        rows, versions = self.rows(self.feed())
        self.assertEqual(rows["App versions"]["health"], "ok")
        self.assertIn("All 11", rows["App versions"]["text"])
        self.assertEqual(versions["behind"], [])

    def test_no_release_list_warns_on_the_server(self):
        rows, versions = self.rows(None)
        self.assertEqual(rows["Release list"]["health"], "warn")
        self.assertIsNone(versions)

    def test_a_list_not_read_for_hours_warns(self):
        rows, _ = self.rows(self.feed(fetched=datetime.now(timezone.utc) - timedelta(hours=7)))
        self.assertEqual(rows["Release list"]["health"], "warn")

    def test_an_overdue_release_warns_and_names_the_app(self):
        old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        rows, _ = self.rows(self.feed(wordcraft={"version": "99.0.0", "published_at": old}))
        self.assertEqual(rows["App versions"]["health"], "warn")
        self.assertIn("wordcraft", rows["App versions"]["text"])

    def test_evals_for_other_versions_warn(self):
        apps = [{"app": name, "version": version} for name, version in self.pins().items()]
        fresh = {"apps": apps, "stale": False, "date": "4 November 2026"}
        self.assertEqual(self.rows(self.feed(), fresh)[0]["App checks"]["health"], "ok")
        moved = {**fresh, "apps": [{**apps[0], "version": "0.0.1"}, *apps[1:]]}
        row = self.rows(self.feed(), moved)[0]["App checks"]
        self.assertEqual(row["health"], "warn")
        self.assertIn(apps[0]["app"], row["text"])


class BumpTests(unittest.TestCase):
    def test_bumping_one_app_leaves_the_others_alone(self):
        bump = load_script("bump_apps")
        text = (ROOT / "craft_apps.json").read_text()
        apps = json.loads(text)["apps"]
        app = apps[0]
        digests = {f"{app['name']}-9.9.9-linux-{arch}.tar.gz": arch[0] * 64 for arch in bump.ARCHES}
        moved = json.loads(bump.bump_manifest(text, app, "9.9.9", digests))["apps"]
        self.assertEqual(moved[0]["version"], "9.9.9")
        self.assertEqual(moved[0]["sha256"]["x86_64"], "x" * 64)
        self.assertEqual(moved[0]["confinement_checked"], "pending the release-watch run")
        self.assertEqual(moved[0]["hidden_tools"], app["hidden_tools"])
        self.assertEqual(moved[1:], apps[1:])

    def test_a_bump_puts_vectorcraft_script_tools_back(self):
        bump = load_script("bump_apps")
        text = (ROOT / "craft_apps.json").read_text()
        apps = json.loads(text)["apps"]
        app = next(entry for entry in apps if entry["name"] == "vectorcraft")
        digests = {f"vectorcraft-9.9.9-linux-{arch}.tar.gz": arch[0] * 64 for arch in bump.ARCHES}
        kept = json.loads(bump.bump_manifest(text, app, "9.9.9", digests))["apps"]
        self.assertEqual(next(entry for entry in kept if entry["name"] == "vectorcraft")["hidden_tools"], app["hidden_tools"])
        start = text.index('"name": "vectorcraft"')
        end = text.find('"name": "', start + 10)
        block = re.sub(
            r'"hidden_tools": \[(.*?)]', '"hidden_tools": [\n        "ui_inspect"\n      ]',
            text[start:end], count=1, flags=re.S,
        )
        stripped = text[:start] + block + text[end:]
        regenerated = {**app, "hidden_tools": ["ui_inspect"]}
        moved = json.loads(bump.bump_manifest(stripped, regenerated, "9.9.9", digests))["apps"]
        got = next(entry for entry in moved if entry["name"] == "vectorcraft")
        self.assertEqual(got["hidden_tools"], ["ui_inspect", "run_command", "script.run"])
        self.assertEqual(got["mcp_args"], app["mcp_args"])
        self.assertEqual([entry for entry in moved if entry["name"] != "vectorcraft"],
                         [entry for entry in apps if entry["name"] != "vectorcraft"])

    def test_the_readme_table_follows(self):
        bump = load_script("bump_apps")
        app = {"name": "wordcraft", "title": "WordCraft", "version": "0.15.1"}
        self.assertEqual(bump.bump_readme("| WordCraft 0.15.1 | Words |", app, "0.16.0", {}), "| WordCraft 0.16.0 | Words |")

    def test_the_pull_request_records_a_real_sweep_and_marks_failures(self):
        pr = load_script("release_watch_pr")
        text = '{"name": "a", "confinement_checked": "pending the release-watch run"}, {"name": "b", "confinement_checked": "pending the release-watch run"}'
        sweep = {"attempts": 9, "path_tools": ["x"], "path_commands": [], "escapes": []}
        recorded = pr.record_sweep(text, "b", "1.0", sweep)
        self.assertIn('"name": "a", "confinement_checked": "pending the release-watch run"', recorded)
        self.assertIn("9 attempts through 1 tools and 0 engine commands, 0 reached", recorded)


if __name__ == "__main__":
    unittest.main()
