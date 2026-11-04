# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Adithya Shankaran

import re
import subprocess
import sys
import unittest
from pathlib import Path

from starlette.testclient import TestClient

import app as app_module
import craft_sessions

ROOT = Path(__file__).resolve().parent.parent
linux_only = unittest.skipUnless(sys.platform == "linux", "uses Linux process controls")


class HeaderTests(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()

    def test_every_page_has_a_policy_with_its_own_nonce(self):
        first, second = self.client.get("/"), self.client.get("/")
        nonce = re.search(r"'nonce-([^']+)'", first.headers["Content-Security-Policy"]).group(1)
        self.assertIn(f'nonce="{nonce}"'.encode(), first.data)
        self.assertNotIn(nonce.encode(), second.data)
        for directive in ("default-src 'none'", "frame-ancestors 'none'", "base-uri 'none'"):
            self.assertIn(directive, first.headers["Content-Security-Policy"])
        self.assertNotIn("script-src 'unsafe-inline'", first.headers["Content-Security-Policy"])

    def test_no_inline_script_goes_without_the_nonce(self):
        for path in ("/", "/status", "/privacy", "/terms", "/support"):
            page = self.client.get(path, headers={"Accept": "text/html"}).data.decode()
            for tag in re.findall(r"<script\b[^>]*>", page):
                self.assertTrue("nonce=" in tag or "src=" in tag, f"{path}: {tag}")
            self.assertNotRegex(page, r"<[a-z]+[^>]*\son(click|load|error)=")

    def test_the_connector_endpoints_get_the_shared_headers_too(self):
        # The wrapper around the whole server, so /mcp and /studio get them as /health does.
        client = TestClient(app_module.asgi_app)
        headers = client.get("/health").headers
        self.assertEqual(headers["x-content-type-options"], "nosniff")
        self.assertIn("max-age", headers["strict-transport-security"])

        async def plain(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"{}"})

        self.assertEqual(TestClient(app_module.security_headers(plain)).post("/studio").headers["x-content-type-options"], "nosniff")


@linux_only
class SessionTests(unittest.TestCase):
    def run_server(self, child: str) -> subprocess.CompletedProcess:
        """A stand-in server with a secret in its environment, and one session started the real way."""
        code = f"""
import subprocess, sys
import craft_sessions
craft_sessions.network_is_cut()
craft_sessions.protect_server()
done = subprocess.run([sys.executable, "-I", "-c", {child!r}], preexec_fn=craft_sessions._limit_child, capture_output=True, text=True)
print(done.stdout, end="")
print(done.stderr, end="", file=sys.stderr)
sys.exit(done.returncode)
"""
        return subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True,
                              env={"SERVER_SECRET": "hunter2", "PATH": "/usr/bin:/bin", "PYTHONPATH": str(ROOT)}, timeout=60)

    def test_a_session_cant_read_the_servers_environment_or_memory(self):
        done = self.run_server("""
import os
for name in ("environ", "mem"):
    try:
        data = open(f"/proc/{os.getppid()}/{name}", "rb").read(4096)
        print(name, "READ", b"hunter2" in data)
    except OSError as exc:
        print(name, type(exc).__name__)
""")
        self.assertEqual(done.stdout.split(), ["environ", "PermissionError", "mem", "PermissionError"], done.stderr)

    def test_a_session_keeps_its_own_process_settings(self):
        done = self.run_server("""
import ctypes
libc = ctypes.CDLL(None)
print(libc.prctl(3, 0, 0, 0, 0), libc.prctl(39, 0, 0, 0, 0))
""")
        # Dumpable again, for its own /proc files, and no new privileges.
        self.assertEqual(done.stdout.split(), ["1", "1"], done.stderr)

    @unittest.skipUnless(craft_sessions.network_is_cut(), "this host doesn't allow it")
    def test_a_session_has_no_network_but_can_still_write_files(self):
        done = self.run_server("""
import os, socket, tempfile
print(sorted(os.listdir("/sys/class/net")) == ["lo"] or "own-namespace")
with socket.socket() as sock:
    sock.settimeout(2)
    try:
        sock.connect(("1.1.1.1", 443))
        print("CONNECTED")
    except OSError:
        print("refused")
with tempfile.NamedTemporaryFile(dir="/tmp") as handle:
    handle.write(b"x")
    print("wrote", os.stat(handle.name).st_uid == os.getuid())
""")
        self.assertIn("refused", done.stdout, done.stderr)
        self.assertNotIn("CONNECTED", done.stdout)
        self.assertIn("wrote True", done.stdout, done.stderr)


if __name__ == "__main__":
    unittest.main()
