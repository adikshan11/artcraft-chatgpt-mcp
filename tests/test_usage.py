# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Adithya Shankaran

import json
import shutil
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from starlette.testclient import TestClient as AsgiTestClient

import usage
from media_storage import LocalStorage, S3Storage, StorageError, user_id_for


def reset():
    usage._hours.clear()
    usage._written.clear()
    usage._finished.clear()
    usage._summary.clear()
    usage._storage.clear()
    usage._refreshing.clear()
    usage._first_request["pending"] = True


class UsageTestCase(unittest.TestCase):
    def setUp(self):
        reset()
        self.tmp = Path(tempfile.mkdtemp())
        self.store = LocalStorage(self.tmp / "store")

    def tearDown(self):
        reset()
        shutil.rmtree(self.tmp)

    def stored(self) -> str:
        return "".join(self.store.get_record(key).decode() for key in self.store.list_records(usage.PREFIX))

    def test_paths_become_fixed_route_names(self):
        self.assertEqual(usage.route_name("/"), "home")
        self.assertEqual(usage.route_name("/studio"), "studio connector")
        self.assertEqual(usage.route_name("/studio/"), "studio connector")
        self.assertEqual(usage.route_name("/mcp/"), "catalog connector")
        self.assertEqual(usage.route_name("/static/site.js"), "static files")
        self.assertEqual(usage.route_name("/.well-known/oauth-protected-resource/studio"), "sign-in metadata")
        # A look-alike prefix and unknown paths don't take a section's name.
        self.assertEqual(usage.route_name("/studiofoo"), "other")
        self.assertEqual(usage.route_name("/wp-admin/setup.php"), "other")

    def test_counts_requests_errors_and_cold_starts(self):
        usage.record_request("/", 200, 12)
        usage.record_request("/", 404, 3)
        usage.record_request("/studio", 500, 40)
        result = usage.summary(None)
        self.assertEqual((result["requests"], result["client_errors"], result["server_errors"]), (3, 1, 1))
        self.assertEqual(result["cold_starts"], 1)
        home = next(row for row in result["routes"] if row["route"] == "home")
        self.assertEqual((home["count"], home["p50_ms"]), (2, 8))
        self.assertEqual(len(result["timeline"]), 24)
        self.assertEqual(result["timeline"][-1]["requests"], 3)

    def test_records_hold_no_paths_ids_or_inputs(self):
        user = user_id_for("auth0", "auth0|secret-person")
        usage.record_request(f"/studio/files/{user}?token=secret-token", 200, 5)
        usage.record_request("/wp-admin/secret-path.php", 404, 1)
        usage.record_tool("studio", "run_craft_tool", "Effectcraft", True, 900)
        usage.flush(self.store)
        written = self.stored()
        # Control: the record was written and holds what it should.
        self.assertIn("studio connector", written)
        self.assertIn("run_craft_tool", written)
        for secret in (user, "secret-person", "secret-token", "secret-path", "wp-admin", "/studio/files"):
            self.assertNotIn(secret, written)

    def test_tool_calls_keep_name_app_and_outcome_only(self):
        class Server:
            async def list_tools(self):
                return [type("Tool", (), {"name": name})() for name in ("open_craft_session", "refused", "broken")]

            async def call_tool(self, name, arguments, context=None):
                if name in ("broken", "secret-tool-name"):
                    raise RuntimeError("no")
                return type("Result", (), {"is_error": name == "refused"})()

        server = Server()
        usage.timed_tools(server, "studio", lambda arguments: arguments.get("app"))
        usage.timed_tools(server, "studio", lambda arguments: arguments.get("app"))  # wrapping twice counts once

        import asyncio

        async def run():
            await server.call_tool("open_craft_session", {"app": "Effectcraft", "prompt": "secret prompt"})
            await server.call_tool("refused", {})
            with self.assertRaises(RuntimeError):
                await server.call_tool("broken", {"app": "Effectcraft"})
            # A caller can send any name; it must not reach a record.
            with self.assertRaises(RuntimeError):
                await server.call_tool("secret-tool-name", {})

        asyncio.run(run())
        result = usage.summary(None)
        tools = {row["tool"]: row for row in result["tools"]}
        self.assertEqual(tools["studio/open_craft_session"]["calls"], 1)
        self.assertEqual(tools["studio/refused"]["failed"], 1)
        self.assertEqual(tools["studio/broken"]["failed"], 1)
        self.assertEqual(tools[f"studio/{usage.UNKNOWN_TOOL}"]["failed"], 1)
        self.assertEqual(result["apps"], [{"app": "Effectcraft", "calls": 2, "failed": 1}])
        usage.flush(self.store)
        self.assertNotIn("secret prompt", self.stored())
        self.assertNotIn("secret-tool-name", self.stored())
        self.assertIn("studio/refused", self.stored())

    def test_health_checks_are_counted_apart(self):
        for _ in range(5):
            usage.record_request("/health", 200, 1)
        usage.record_request("/", 200, 10)
        result = usage.summary(None)
        self.assertEqual((result["requests"], result["health_checks"]), (1, 5))
        self.assertEqual(result["timeline"][-1]["requests"], 1)
        self.assertEqual({row["route"]: row["count"] for row in result["routes"]}, {"health": 5, "home": 1})

    def test_local_and_bucket_totals(self):
        self.store.put_record(f"{usage.PREFIX}2026-11-04T10/{'a' * 12}.json", b"12345")
        self.assertEqual(self.store.total_bytes(), {"bytes": 5, "files": 1, "old_versions": 0})

        class Pages:
            def paginate(self, Bucket):
                return [{"Versions": [{"Size": 100, "IsLatest": True}, {"Size": 40, "IsLatest": False}]},
                        {"DeleteMarkers": [{"Key": "gone"}]},
                        {"Versions": [{"Size": 2, "IsLatest": True}]}]

        bucket = S3Storage.__new__(S3Storage)
        bucket.bucket = "test"
        bucket.client = type("Client", (), {"get_paginator": lambda self, name: Pages()})()
        self.assertEqual(bucket.total_bytes(), {"bytes": 142, "files": 2, "old_versions": 1})

    def test_slow_bucket_does_not_hold_up_the_page(self):
        release = threading.Event()

        class Slow(LocalStorage):
            def list_records(self, prefix):
                release.wait(10)
                return []

        with patch.object(usage, "SUMMARY_WAIT_SECONDS", 0.05):
            started = time.monotonic()
            self.assertIsNone(usage.cached_summary(Slow(self.tmp / "slow")))
            self.assertLess(time.monotonic() - started, 2)
            release.set()
            usage._refreshing[24].join(5)
            # Control: once the read finishes, the counts are there.
            self.assertEqual(usage.cached_summary(Slow(self.tmp / "slow"))["requests"], 0)

    def test_failed_read_keeps_the_last_counts(self):
        usage.record_request("/", 200, 1)
        first = usage.cached_summary(self.store)
        self.assertEqual(first["requests"], 1)
        usage._summary[24] = (time.monotonic() - usage.SUMMARY_SECONDS - 1, first)
        with patch.object(self.store, "list_records", side_effect=OSError("down")), self.assertLogs("bridgecraft.usage", "WARNING"):
            self.assertEqual(usage.cached_summary(self.store), first)

    def test_flush_writes_once_and_merges_processes(self):
        usage.record_request("/", 200, 10)
        usage.flush(self.store)
        keys = self.store.list_records(usage.PREFIX)
        self.assertEqual(len(keys), 1)
        with patch.object(self.store, "put_record", side_effect=AssertionError("unchanged hour rewritten")):
            usage.flush(self.store)
        # Another process's record for the same hour is added in.
        hour = keys[0].split("/")[2]
        other = {"requests": {"home": {"count": 4, "client_errors": 0, "server_errors": 0, "ms": [20]}},
                 "tools": {}, "apps": {}, "cold_starts": 1}
        self.store.put_record(f"{usage.PREFIX}{hour}/{'b' * 12}.json", json.dumps(other).encode())
        result = usage.summary(self.store)
        self.assertEqual((result["requests"], result["cold_starts"]), (5, 2))

    def test_finished_hours_are_forgotten_after_writing(self):
        old = usage.hour_of(datetime.now(timezone.utc) - timedelta(hours=3))
        usage._hours[old] = {"requests": {"home": {"count": 1, "client_errors": 0, "server_errors": 0, "ms": [1]}},
                             "tools": {}, "apps": {}, "cold_starts": 0}
        usage.flush(self.store)
        self.assertNotIn(old, usage._hours)
        self.assertEqual(usage.summary(self.store)["requests"], 1)

    def test_prune_deletes_only_old_records(self):
        old = usage.hour_of(datetime.now(timezone.utc) - timedelta(days=usage.RETENTION_DAYS, hours=1))
        recent = usage.hour_of(datetime.now(timezone.utc) - timedelta(days=1))
        for hour in (old, recent):
            self.store.put_record(f"{usage.PREFIX}{hour}/{'c' * 12}.json", b"{}")
        self.assertEqual(usage.prune(self.store), 1)
        self.assertEqual([key.split("/")[2] for key in self.store.list_records(usage.PREFIX)], [recent])

    def test_record_keys_stay_under_ops(self):
        for key in ("users/abc/media/x", "ops/usage/../../users/x.json", "ops/usage/2026-11-01T10/short.json"):
            with self.assertRaises(StorageError):
                self.store.put_record(key, b"{}")
        self.assertEqual(self.store.list_records("users/"), [])
        self.assertEqual(self.store.list_records("ops/../users/"), [])

    def test_failed_write_is_logged_not_raised(self):
        import asyncio

        usage.record_request("/", 200, 1)
        broken = LocalStorage(self.tmp / "store")
        with patch.object(broken, "put_record", side_effect=OSError("down")), self.assertLogs("bridgecraft.usage", "WARNING"):
            asyncio.run(usage.flush_now(lambda: broken))
        # Kept in memory and written next time.
        usage.flush(self.store)
        self.assertEqual(usage.summary(self.store)["requests"], 1)


class SiteUsageTestCase(unittest.TestCase):
    def setUp(self):
        reset()
        self.tmp = Path(tempfile.mkdtemp())
        self.store = LocalStorage(self.tmp / "store")
        self.patch = patch("studio_mcp.storage", return_value=self.store)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        reset()
        shutil.rmtree(self.tmp)

    def test_site_requests_are_counted_and_shown_in_status_json(self):
        from app import create_asgi_app

        # Without the lifespan: the connectors' session managers run once per process.
        client = AsgiTestClient(create_asgi_app())
        client.get("/")
        client.get("/no-such-page")
        usage._summary.clear()
        body = client.get("/status.json").json()
        routes = {row["route"]: row["count"] for row in body["usage"]["routes"]}
        self.assertEqual(routes["home"], 1)
        self.assertEqual(routes["other"], 1)
        self.assertEqual(body["usage"]["client_errors"], 1)
        usage.flush(self.store)
        self.assertTrue(self.store.list_records(usage.PREFIX))

    def test_status_page_draws_the_counts(self):
        from app import app

        for _ in range(3):
            usage.record_request("/", 200, 10)
        usage.record_request("/health", 200, 1)
        usage.record_tool("studio", "run_craft_tool", "wordcraft", True, 50)
        page = app.test_client().get("/status").get_data(as_text=True)
        self.assertIn('id="status-usage"', page)
        self.assertIn("Requests per hour", page)
        self.assertIn("studio/run_craft_tool", page)
        self.assertIn("WordCraft", page)
        self.assertIn("(1 in these 24 hours)", page)
        self.assertIn("File storage:</strong>", page)
        hour = usage.hour_of(datetime.now(timezone.utc))[11:]
        self.assertIn(f'data-tip="{hour}:00 UTC: 3 requests"', page)

    def test_status_page_with_nothing_counted_and_with_no_counts(self):
        from app import app

        usage._first_request["pending"] = False
        page = app.test_client().get("/status").get_data(as_text=True)
        self.assertIn("None in the last 24 hours.", page)
        self.assertNotIn('class="usage-bar"', page)
        with patch("site_status.usage_counts", return_value=None):
            page = app.test_client().get("/status").get_data(as_text=True)
        self.assertIn("usage counts can't be read", page)
        self.assertIn('id="status-apps"', page)

    def test_chart_and_size_wording(self):
        import site_status

        timeline = [{"hour": "2026-11-04T09", "requests": 0}, {"hour": "2026-11-04T10", "requests": 40}, {"hour": "2026-11-04T11", "requests": 10}]
        chart = site_status._chart(timeline, "requests", "requests")
        self.assertEqual((chart["total"], chart["highest"]), (50, 40))
        self.assertEqual([bar["height"] for bar in chart["bars"]], [0, 100, 25])
        self.assertEqual(chart["bars"][1]["hour"], "10:00")
        self.assertEqual(site_status._chart(timeline[:1], "requests", "requests")["bars"][0]["height"], 0)
        self.assertEqual([site_status._size(n) for n in (5, 1500, 3_400_000, 10 * 1000**3)], ["5 bytes", "1.5 KB", "3.4 MB", "10 GB"])


if __name__ == "__main__":
    unittest.main()
