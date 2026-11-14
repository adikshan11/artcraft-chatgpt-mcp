# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Adithya Shankaran

import asyncio
import re
import unittest
from pathlib import Path
from unittest import mock
from urllib import error

from starlette.testclient import TestClient

import app as app_module
import artcraft_api
import media_storage
import resilience

ROOT = Path(__file__).resolve().parent.parent


class Clock:
    """A clock the test moves by hand."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class CircuitBreakerTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.breaker = resilience.CircuitBreaker("Thing", threshold=3, cooldown=30, clock=self.clock)

    def test_it_opens_after_the_threshold_and_refuses_with_a_wait(self):
        for _ in range(3):
            self.breaker.before()
            self.breaker.failure()
        with self.assertRaises(resilience.CircuitOpen) as caught:
            self.breaker.before()
        self.assertEqual(caught.exception.retry_after, 30)

    def test_a_success_in_between_keeps_it_closed(self):
        for _ in range(10):
            self.breaker.failure()
            self.breaker.failure()
            self.breaker.success()
        self.breaker.before()

    def test_after_the_cooldown_one_probe_goes_through_and_a_success_closes_it(self):
        for _ in range(3):
            self.breaker.failure()
        self.clock.now += 31
        self.breaker.before()
        with self.assertRaises(resilience.CircuitOpen):
            self.breaker.before()  # Only one probe at a time.
        self.breaker.success()
        self.breaker.before()
        self.assertFalse(self.breaker.is_open)

    def test_a_failed_probe_keeps_it_open_for_another_cooldown(self):
        for _ in range(3):
            self.breaker.failure()
        self.clock.now += 31
        self.breaker.before()
        self.breaker.failure()
        self.clock.now += 10
        with self.assertRaises(resilience.CircuitOpen):
            self.breaker.before()


class UpstreamBreakerTests(unittest.TestCase):
    def setUp(self):
        artcraft_api.breaker.success()
        media_storage.breaker.success()
        self.addCleanup(artcraft_api.breaker.success)
        self.addCleanup(media_storage.breaker.success)

    def test_artcraft_stops_being_called_after_repeated_network_failures(self):
        opener = mock.Mock()
        opener.open.side_effect = error.URLError("down")
        with mock.patch.object(artcraft_api.request, "build_opener", return_value=opener):
            for _ in range(5):
                with self.assertRaisesRegex(artcraft_api.ArtCraftAPIError, "Could not reach"):
                    artcraft_api._request_json("/v1/x", "GET", auth=False)
            calls = opener.open.call_count
            with self.assertRaisesRegex(artcraft_api.ArtCraftAPIError, "not responding right now"):
                artcraft_api._request_json("/v1/x", "GET", auth=False)
        self.assertEqual(opener.open.call_count, calls)

    def test_artcraft_refusing_a_request_does_not_count_as_down(self):
        opener = mock.Mock()
        opener.open.side_effect = error.HTTPError("https://x", 400, "bad", {}, None)
        with mock.patch.object(artcraft_api.request, "build_opener", return_value=opener):
            for _ in range(10):
                with self.assertRaises(artcraft_api.ArtCraftAPIError):
                    artcraft_api._request_json("/v1/x", "GET", auth=False)
        self.assertFalse(artcraft_api.breaker.is_open)

    def test_storage_stops_after_server_errors_but_not_after_a_missing_file(self):
        from botocore.exceptions import ClientError, EndpointConnectionError

        calls = []

        def broken():
            calls.append(1)
            raise EndpointConnectionError(endpoint_url="https://s3")

        for _ in range(5):
            with self.assertRaises(EndpointConnectionError):
                media_storage._guarded(broken)
        with self.assertRaisesRegex(media_storage.StorageError, "not responding"):
            media_storage._guarded(broken)
        self.assertEqual(len(calls), 5)

        media_storage.breaker.success()
        missing = ClientError({"Error": {"Code": "NoSuchKey"}, "ResponseMetadata": {"HTTPStatusCode": 404}}, "GetObject")
        for _ in range(10):
            with self.assertRaises(ClientError):
                media_storage._guarded(mock.Mock(side_effect=missing))
        self.assertFalse(media_storage.breaker.is_open)


class LoadGuardTests(unittest.TestCase):
    @staticmethod
    def served(delay=0.0, hold=None):
        async def inner(scope, receive, send):
            if hold is not None:
                await hold.wait()
            elif delay:
                await asyncio.sleep(delay)
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        return inner

    @staticmethod
    async def call(app, path="/", client=("1.2.3.4", 1), headers=()):
        sent = []

        async def send(message):
            sent.append(message)

        async def receive():
            return {"type": "http.request", "body": b""}

        await app({"type": "http", "path": path, "client": client, "headers": list(headers)}, receive, send)
        start = sent[0]
        return start["status"], dict(start["headers"])

    def test_a_client_over_its_limit_gets_429_with_retry_after_and_others_do_not(self):
        app = resilience.load_guard(self.served(), limit=3, window=10, max_inflight=50, header_name="")

        async def run():
            first = [(await self.call(app))[0] for _ in range(3)]
            status, headers = await self.call(app)
            other = (await self.call(app, client=("5.6.7.8", 2)))[0]
            return first, status, headers, other

        first, status, headers, other = asyncio.run(run())
        self.assertEqual(first, [200, 200, 200])
        self.assertEqual(status, 429)
        self.assertTrue(1 <= int(headers[b"retry-after"]) <= 10)
        self.assertEqual(other, 200)

    def test_the_health_check_is_never_limited(self):
        app = resilience.load_guard(self.served(), limit=1, window=10, max_inflight=1, header_name="")

        async def run():
            return [(await self.call(app, "/health"))[0] for _ in range(20)]

        self.assertEqual(set(asyncio.run(run())), {200})

    def test_beyond_the_concurrency_cap_the_answer_is_503_with_retry_after(self):
        async def run():
            hold = asyncio.Event()
            app = resilience.load_guard(self.served(hold=hold), limit=1000, window=10, max_inflight=2, header_name="")
            busy = [asyncio.create_task(self.call(app, client=(f"9.9.9.{i}", i))) for i in range(2)]
            await asyncio.sleep(0.05)
            shed = await self.call(app, client=("8.8.8.8", 9))
            connector = asyncio.create_task(self.call(app, "/studio", client=("7.7.7.7", 8)))
            await asyncio.sleep(0.05)
            hold.set()
            return shed, await connector, [await task for task in busy]

        shed, connector, busy = asyncio.run(run())
        self.assertEqual(shed[0], 503)
        self.assertEqual(shed[1][b"retry-after"], b"2")
        self.assertEqual(connector[0], 200)  # Connector paths are not shed.
        self.assertEqual([status for status, _ in busy], [200, 200])

    def test_the_count_comes_back_down_after_requests_finish(self):
        app = resilience.load_guard(self.served(), limit=1000, window=10, max_inflight=1, header_name="")

        async def run():
            return [(await self.call(app, client=(f"1.1.1.{i}", i)))[0] for i in range(5)]

        self.assertEqual(set(asyncio.run(run())), {200})

    def test_the_named_header_is_what_clients_are_told_apart_by(self):
        app = resilience.load_guard(self.served(), limit=1, window=10, max_inflight=50, header_name="cf-connecting-ip")

        async def run():
            a = [(await self.call(app, client=("10.0.0.1", 1), headers=[(b"cf-connecting-ip", b"2.2.2.2")]))[0] for _ in range(2)]
            b = (await self.call(app, client=("10.0.0.1", 1), headers=[(b"cf-connecting-ip", b"3.3.3.3")]))[0]
            return a, b

        a, b = asyncio.run(run())
        self.assertEqual(a, [200, 429])
        self.assertEqual(b, 200)

    def test_forty_parallel_status_requests_are_served_under_the_default_limits(self):
        client = TestClient(app_module.asgi_app)
        self.assertEqual({client.get("/status.json").status_code for _ in range(40)}, {200})

    def test_the_table_of_clients_stays_bounded(self):
        limiter = resilience.RateLimiter(5, 60)
        for i in range(resilience.MAX_TRACKED_CLIENTS + 500):
            limiter.check(f"client-{i}")
        self.assertLessEqual(len(limiter._seen), resilience.MAX_TRACKED_CLIENTS)


class PageTests(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()

    def test_every_page_starts_with_a_skip_link_that_lands_on_its_main_content(self):
        for path in ("/", "/privacy", "/terms", "/support", "/status"):
            page = self.client.get(path).data.decode()
            link = re.search(r'<a class="skip-link" href="#([^"]+)"', page)
            self.assertIsNotNone(link, path)
            self.assertIn(f'id="{link.group(1)}"', page, path)
            self.assertLess(page.index("skip-link"), page.index("<nav"), path)

    def test_the_connector_endpoints_are_not_frameable_and_load_nothing(self):
        async def plain(scope, receive, send):
            await send({"type": "http.response.start", "status": 401, "headers": []})
            await send({"type": "http.response.body", "body": b"{}"})

        for response in (TestClient(app_module.security_headers(plain)).post("/studio"), TestClient(app_module.asgi_app).get("/health")):
            self.assertEqual(response.headers["x-frame-options"], "DENY")
            self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])
        self.assertEqual(
            TestClient(app_module.security_headers(plain)).post("/studio").headers["content-security-policy"],
            "default-src 'none'; frame-ancestors 'none'",
        )

    def test_a_page_keeps_its_own_policy_and_gets_no_second_one(self):
        response = TestClient(app_module.asgi_app).get("/")
        self.assertEqual(len(response.headers.get_list("content-security-policy")), 1)
        self.assertIn("nonce-", response.headers["content-security-policy"])

    def test_hsts_covers_subdomains(self):
        self.assertIn("includeSubDomains", TestClient(app_module.asgi_app).get("/health").headers["strict-transport-security"])


class BuildTests(unittest.TestCase):
    def test_the_image_is_named_by_digest_and_the_lock_is_installed_with_hashes(self):
        dockerfile = (ROOT / "Dockerfile").read_text()
        froms = re.findall(r"^FROM (\S+)", dockerfile, re.M)
        self.assertTrue(froms and all(re.search(r"@sha256:[0-9a-f]{64}$", name) for name in froms), froms)
        self.assertIn("--require-hashes -r requirements.lock", dockerfile)

    def test_every_locked_package_has_a_hash_and_covers_the_requirements(self):
        lock = (ROOT / "requirements.lock").read_text()
        pins = re.findall(r"^([A-Za-z0-9_.\-]+)==\S+ \\\n(?:    --hash=sha256:[0-9a-f]{64}[ \\]*\n)+", lock, re.M)
        self.assertEqual(len(pins), len(re.findall(r"^[A-Za-z0-9_.\-]+==", lock, re.M)))
        wanted = {re.split(r"[\[<>=]", line)[0].lower().replace("_", "-") for line in (ROOT / "requirements.txt").read_text().split() if line[:1].isalpha()}
        self.assertTrue(wanted <= {name.lower().replace("_", "-") for name in pins}, wanted)


if __name__ == "__main__":
    unittest.main()


class StaticOriginTests(unittest.TestCase):
    def test_only_a_plain_https_address_is_accepted(self):
        accept = app_module._static_origin
        self.assertEqual(accept("https://example.github.io/"), "https://example.github.io")
        for bad in ("", None, "http://example.com", "https://example.com/path", "https://example.com?x=1",
                    "javascript:alert(1)", "https://exa mple.com", "https://example.com;img-src *"):
            self.assertEqual(accept(bad), "", bad)

    def test_with_a_cdn_the_links_and_the_policy_use_it_and_without_one_nothing_changes(self):
        client = app_module.app.test_client()
        plain = client.get("/")
        self.assertNotIn(b"github.io", plain.data)
        self.assertIn(b"/static/style.css?v=", plain.data)
        with mock.patch.object(app_module, "STATIC_ORIGIN", "https://cdn.example.test"), \
                mock.patch.object(app_module, "STATIC_PATH_PREFIX", "/bridgecraft"):
            page = client.get("/")
        text = page.data.decode()
        self.assertRegex(text, r'href="https://cdn\.example\.test/bridgecraft/static/style\.css\?v=[0-9a-f]{10}"')
        self.assertIn('content="https://cdn.example.test/bridgecraft/static/og.jpg', text)
        policy = page.headers["Content-Security-Policy"]
        for directive in ("script-src 'self' https://cdn.example.test", "style-src 'self' https://cdn.example.test",
                          "font-src 'self' https://cdn.example.test", "img-src 'self' https://cdn.example.test"):
            self.assertIn(directive, policy)
        self.assertNotIn("connect-src 'self' https://cdn", policy)
        self.assertNotIn("https://cdn.example.test", plain.headers["Content-Security-Policy"])
