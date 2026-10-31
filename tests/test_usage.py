# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Adithya Shankaran

import json
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from starlette.testclient import TestClient as AsgiTestClient

import usage
from media_storage import LocalStorage, StorageError, user_id_for


def reset():
    usage._hours.clear()
    usage._written.clear()
    usage._finished.clear()
    usage._summary.clear()
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
            async def call_tool(self, name, arguments, context=None):
                if name == "broken":
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

        asyncio.run(run())
        result = usage.summary(None)
        tools = {row["tool"]: row for row in result["tools"]}
        self.assertEqual(tools["studio/open_craft_session"]["calls"], 1)
        self.assertEqual(tools["studio/refused"]["failed"], 1)
        self.assertEqual(tools["studio/broken"]["failed"], 1)
        self.assertEqual(result["apps"], [{"app": "Effectcraft", "calls": 2, "failed": 1}])
        usage.flush(self.store)
        self.assertNotIn("secret prompt", self.stored())

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


if __name__ == "__main__":
    unittest.main()
