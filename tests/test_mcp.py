# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Adithya Shankaran

import base64
import json
import os
import re
import subprocess
import tempfile
import unittest
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from mcp import Client
from mcp.types import CallToolResult, TextContent, Tool
from mcp.server.mcpserver.exceptions import ToolError
from starlette.testclient import TestClient as AsgiTestClient

from app import (
    create_asgi_app,
    ARTCRAFT_APP_DESCRIPTION,
    BRAND_ICON_URL,
    BRAND_PNG_URL,
    ARTCRAFT_PRODUCT_URL,
    ARTCRAFT_SITE_URL,
    GitHubRequestError,
    app,
    asgi_app,
    mcp,
    mcp_allowed_hosts,
)
from artcraft_api import ArtCraftAPIError, get_api_key
from craft_app_mcp import PhotoCraftBridge, _photocraft_parameters
from local_artcraft_mcp import BRAND_ICON_URL, server as local_mcp
from pdfcraft_mcp import _pdf_path

FREE_ESTIMATE = (
    b'{"success":true,"cost_in_credits":null,"cost_in_usd_cents":null,"is_free":true,'
    b'"is_unlimited":true,"is_rate_limited":false,"has_watermark":false}'
)
PAID_ESTIMATE = (
    b'{"success":true,"cost_in_credits":6,"cost_in_usd_cents":6,"is_free":false,'
    b'"is_unlimited":false,"is_rate_limited":false,"has_watermark":false}'
)


class McpServerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        app.config.update(TESTING=True)
        self.client = app.test_client()

    def test_moved_to_forwards_everything_but_health(self):
        with patch.dict(os.environ, {"MOVED_TO": "https://new.example/"}):
            moved = create_asgi_app()
        client = AsgiTestClient(moved)
        page = client.get("/support?x=1", follow_redirects=False)
        self.assertEqual(page.status_code, 308)
        self.assertEqual(page.headers["location"], "https://new.example/support?x=1")
        post = client.post("/studio", json={}, follow_redirects=False)
        self.assertEqual(post.status_code, 308)
        self.assertEqual(post.headers["location"], "https://new.example/studio")
        self.assertEqual(client.get("/health").status_code, 200)

    def test_root_icon_paths_serve_the_logo(self):
        # Safari asks for these fixed paths rather than reading the <link> tags.
        for path, kind in (("/favicon.ico", b"\x00\x00\x01\x00"), ("/apple-touch-icon.png", b"\x89PNG"),
                           ("/apple-touch-icon-precomposed.png", b"\x89PNG")):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)
            self.assertTrue(response.data.startswith(kind), path)
            response.close()

    def test_wordmark_is_two_tone(self):
        page = self.client.get("/").data.decode()
        self.assertEqual(page.count('Bridge<span class="craft">Craft</span>'), 2)  # hero and footer

    def test_static_links_are_versioned_and_cached(self):
        page = self.client.get("/").data.decode()
        match = re.search(r'src="/static/bridgecraft.svg\?v=([0-9a-f]{10})"', page)
        self.assertIsNotNone(match)
        cached = self.client.get(f"/static/bridgecraft.svg?v={match.group(1)}")
        self.assertEqual(cached.headers["Cache-Control"], "public, max-age=31536000, immutable")
        # A stale or missing version is not cached for long.
        self.assertNotIn("immutable", self.client.get("/static/bridgecraft.svg?v=0000000000").headers.get("Cache-Control", ""))
        self.assertNotIn("immutable", self.client.get("/static/bridgecraft.svg").headers.get("Cache-Control", ""))
        # Font preloads keep the plain path that style.css uses.
        self.assertIn('href="/static/fonts/archivo.woff2"', page)

    def test_health_endpoint(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"status": "ok", "mcp_endpoint": "/mcp/", "studio_endpoint": "/studio"})

    def test_health_sends_browsers_to_the_status_page(self):
        browser = self.client.get("/health", headers={"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"})
        self.assertEqual(browser.status_code, 302)
        self.assertEqual(browser.headers["Location"], "/status")
        # Render's check and curl send */* or JSON, and keep getting the JSON.
        for accept in ("*/*", "application/json"):
            self.assertEqual(self.client.get("/health", headers={"Accept": accept}).status_code, 200)

    @patch("app.request.urlopen")
    def test_github_token_is_sent_as_bearer_auth(self, urlopen):
        urlopen.return_value.__enter__.return_value.read.return_value = b"{}"
        with patch.dict("os.environ", {"GITHUB_TOKEN": "test-token"}):
            from app import github_get

            github_get("/orgs/storytold")

        github_request = urlopen.call_args.args[0]
        self.assertEqual(github_request.get_header("Authorization"), "Bearer test-token")

    def test_render_hostname_is_added_to_transport_allowlist(self):
        with patch.dict(
            "os.environ",
            {"RENDER_EXTERNAL_HOSTNAME": "artcraft-mcp.example.onrender.com"},
            clear=True,
        ):
            self.assertEqual(
                mcp_allowed_hosts(),
                [
                    "artcraft-mcp.example.onrender.com",
                    "artcraft-mcp.example.onrender.com:*",
                ],
            )

    @patch(
        "app.list_projects",
        return_value=[
            {
                "name": "artcraft",
                "description": "Creative editor",
                "url": "https://github.com/storytold/artcraft",
                "language": "Rust",
                "stars": 1,
                "topics": ["creative"],
                "updated_at": "2026-10-01T00:00:00Z",
            }
        ],
    )
    def test_homepage_presents_unified_artcraft_integration(self, _list_projects):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'<h1>Use <span class="hl">Craft apps</span> from AI apps</h1>', response.data)
        self.assertIn(b"<span>ChatGPT</span><span>Claude</span><span>Gemini</span>", response.data)
        # Plain headings: no eyebrow labels or accent words on the homepage.
        for heading in (b"<h2>Examples</h2>", b"<h2>The apps</h2>", b"<h2>What doesn't work yet</h2>",
                        b"<h2>Who runs this</h2>", b"<h2>Set it up</h2>"):
            self.assertIn(heading, response.data)
        self.assertNotIn(b"<em>", response.data)
        self.assertNotIn(b'class="label', response.data)
        # The back of the phoenix carries ArtCraft's own icon.
        self.assertIn(b'class="flip-mark" src="/static/app-icons/artcraft.svg?v=', response.data)
        self.assertIn(b'class="flip"', response.data)
        for app in (b"WordCraft", b"DeckCraft", b"PdfCraft", b"PhotoCraft", b"VectorCraft",
                    b"DesignCraft", b"FigCraft", b"SoundCraft"):
            self.assertIn(app, response.data)
        self.assertIn(b"v0.12.0", response.data)
        # All 13 Craft apps are on the plate and in the grid; the unhosted ones say why.
        for app in (b"EffectCraft", b"FilmCraft", b"CADCraft", b"GridCraft", b"LightCraft"):
            self.assertIn(app + b" (not hosted yet)", response.data)
        self.assertEqual(response.data.count(b'<li style="--i: '), 13)
        self.assertEqual(response.data.count(b'class="is-off"'), 5)
        self.assertIn(b"<h3 class=\"app-group-title\">Not hosted yet</h3>", response.data)
        self.assertIn(b"Merged, not in 0.19.0", response.data)
        self.assertIn(b'href="https://github.com/storytold/lightcraft"', response.data)
        # Privacy is a footer link, not a homepage section; footer links carry no arrows.
        self.assertNotIn(b'id="privacy"', response.data)
        self.assertNotIn("↗".encode(), response.data)
        # Narrow screens get a menu button; everyone gets a light/dark switch and GitHub.
        self.assertIn(b'class="icon-button nav-menu"', response.data)
        self.assertIn(b"data-theme-toggle", response.data)
        self.assertIn(b'aria-label="Source code on GitHub"', response.data)
        self.assertIn(b'href="https://discord.gg/artcraft"', response.data)
        # Each app I can't host yet links the open upstream pull requests that would fix it.
        for pr in (b"effectcraft/pull/2719", b"filmcraft/pull/1657", b"cadcraft/pull/1904", b"gridcraft/pull/480",
                   b"gridcraft/pull/2739", b"gridcraft/pull/2857", b"lightcraft/pull/2176"):
            self.assertIn(b'href="https://github.com/storytold/' + pr + b'"', response.data)
        self.assertIn(b"/static/site.js", response.data)
        self.assertIn(b'href="/status"', response.data)
        # One example from every hosted app, made by the apps themselves.
        for sample in (b"deck-chart.png", b"report.png", b"pdf-draft.png", b"flyer.png", b"badge.png",
                       b"post.png", b"screen.png", b"jingle-wave.png", b"jingle.mp3"):
            self.assertIn(b"/static/showcase/" + sample, response.data)
        self.assertIn(b'href="https://github.com/storytold/figcraft"', response.data)
        # Limits and status are stated plainly.
        self.assertIn(b"EffectCraft, FilmCraft, CADCraft, GridCraft", response.data)
        self.assertIn(b"Up to 500 MB each and 5 GB per account.", response.data)
        # The disclaimer sits in the footer, not under the hero.
        hero, footer = response.data.split(b'<footer class="footer">', 1)
        self.assertIn(b"Built and run by Adithya Shankaran. Not an official Storytold or ArtCraft product.", footer)
        self.assertNotIn(b"Built and run by", hero)
        self.assertIn(b"https://github.com/storytold/craft-contrib/pull/23", response.data)
        self.assertIn(b"https://bridgecraft.onrender.com/studio", response.data)
        self.assertIn(b"adikshan11/bridgecraft", response.data)
        self.assertIn(b"PDFs and e-signatures", response.data)
        self.assertIn(b"#run-the-local-mcp-server", response.data)
        self.assertIn(b"which runs on Windows, macOS and Linux", response.data)
        self.assertNotIn(b"local server on a Mac", response.data)
        # PNG only: the traced SVG is 700KB and Safari skips it for the tab icon.
        self.assertNotIn(b'rel="icon" href="/static/bridgecraft.svg', response.data)
        self.assertIn(b"/static/bridgecraft-512.png", response.data)
        # Safari asks for the root paths, so the page names those too.
        self.assertIn(b'rel="icon" href="/favicon.ico"', response.data)
        self.assertIn(b'rel="apple-touch-icon" href="/apple-touch-icon.png"', response.data)
        self.assertIn(b"<title>BridgeCraft \xc2\xb7 Craft apps in your AI assistant</title>", response.data)
        self.assertIn(b"/static/og.jpg", response.data)
        self.assertNotIn(b"See how it works", response.data)
        self.assertNotIn(b"ticker", response.data)
        self.assertNotIn(b"artcraft-icon.svg", response.data)
        self.assertIn(
            f'href="{ARTCRAFT_PRODUCT_URL}"'.encode(),
            response.data,
        )
        self.assertIn(ARTCRAFT_PRODUCT_URL.encode(), ARTCRAFT_APP_DESCRIPTION.encode())
        self.assertNotIn(b"Adobe Express", response.data)
        for path in (b'href="/privacy"', b'href="/terms"', b'href="/support"'):
            self.assertIn(path, response.data)

    def test_listing_files_point_at_the_hosted_studio(self):
        root = Path(__file__).resolve().parent.parent
        studio_url = f"{ARTCRAFT_SITE_URL}/studio"
        server = json.loads((root / "server.json").read_text())
        self.assertEqual(server["name"], "io.github.adikshan11/bridgecraft")
        self.assertEqual(server["remotes"], [{"type": "streamable-http", "url": studio_url}])
        marketplace = json.loads((root / ".claude-plugin" / "marketplace.json").read_text())
        entry = marketplace["plugins"][0]
        plugin_dir = root / entry["source"]
        manifest = json.loads((plugin_dir / ".claude-plugin" / "plugin.json").read_text())
        self.assertEqual(entry["name"], manifest["name"])
        self.assertEqual(manifest["version"], server["version"])
        servers = json.loads((plugin_dir / ".mcp.json").read_text())["mcpServers"]
        self.assertEqual(servers, {"bridgecraft": {"type": "http", "url": studio_url}})
        # Anthropic's directory needs a README of 40 words or more and a license in the plugin folder.
        self.assertGreaterEqual(len((plugin_dir / "README.md").read_text().split()), 40)
        self.assertEqual(manifest["license"], "Apache-2.0")

    def test_homepage_offers_installs_for_other_apps(self):
        page = self.client.get("/").get_data(as_text=True)
        self.assertIn("claude plugin install bridgecraft --marketplace adikshan11/bridgecraft", page)
        link = page.split('href="cursor://anysphere.cursor-deeplink/mcp/install?', 1)[1].split('"', 1)[0]
        name, config = (part.split("=", 1)[1] for part in link.split("&amp;"))
        self.assertEqual(name, "bridgecraft")
        self.assertEqual(json.loads(base64.b64decode(config)), {"url": f"{ARTCRAFT_SITE_URL}/studio"})
        self.assertIn(f"""code --add-mcp '{{"name":"bridgecraft","type":"http","url":"{ARTCRAFT_SITE_URL}/studio"}}'""", page)

    def test_status_page_lists_services_and_every_app(self):
        response = self.client.get("/status")
        self.assertEqual(response.status_code, 200)
        for text in (b"<h1>Status</h1>", b"Sign-in", b"File storage", b"File cleanup", b"Open app sessions",
                     b"keeps no request logs", b"/status.json", b"every 30 seconds"):
            self.assertIn(text, response.data)
        for app in (b"WordCraft", b"SoundCraft", b"EffectCraft", b"LightCraft"):
            self.assertIn(app, response.data)
        self.assertIn(b"Not hosted. storytold/effectcraft#2719 is merged.", response.data)
        self.assertIn(b'href="https://github.com/storytold/cadcraft/pull/1904"', response.data)
        self.assertIn(b'data-theme-toggle', response.data)

    @patch.dict(os.environ, {"RENDER": "true", "RENDER_GIT_COMMIT": "abcdef1234567", "OAUTH_ISSUER": "",
                             "S3_BUCKET": ""})
    def test_status_json_reports_problems_without_secrets(self):
        import studio_mcp

        studio_mcp.storage.cache_clear()
        self.addCleanup(studio_mcp.storage.cache_clear)
        with patch.dict(studio_mcp.last_sweep, {"at": datetime(2026, 10, 31, 9, 5, tzinfo=timezone.utc),
                                                "ok": False, "deleted": 0}):
            response = self.client.get("/status.json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        data = response.get_json()
        rows = {row["name"]: row for row in data["services"] + data["apps"]}
        self.assertEqual(data["facts"]["version"], "abcdef1")
        self.assertEqual(rows["Sign-in"]["health"], "warn")
        # Render without a bucket refuses to store files on its throwaway disk.
        self.assertEqual(rows["File storage"]["health"], "error")
        self.assertEqual(rows["File cleanup"]["health"], "error")
        self.assertIn("09:05 UTC", rows["File cleanup"]["text"])
        # The apps aren't installed in the test environment, which on Render is a fault.
        self.assertEqual(rows["WordCraft"]["health"], "error")
        self.assertEqual(rows["EffectCraft"]["health"], "off")
        self.assertEqual(len(data["apps"]), 13)
        self.assertEqual(data["overall"], "error")
        body = response.get_data(as_text=True).lower()
        for secret in ("key", "token", "secret", "password", "bucket", "/opt/craft"):
            self.assertNotIn(secret, body)

    def test_status_counts_open_sessions(self):
        import craft_sessions

        with patch.dict(craft_sessions.manager.sessions, {"s1": object(), "s2": object()}):
            data = self.client.get("/status.json").get_json()
        self.assertEqual(data["facts"]["open_sessions"], 2)
        self.assertEqual(data["facts"]["max_sessions"], craft_sessions.MAX_SESSIONS)

    def test_privacy_terms_and_support_pages(self):
        pages = {
            "/privacy": [b"Privacy policy", b"deleted 7 days after your session ends", b"not operated by Storytold"],
            "/terms": [b"Terms of use", b"500 MB per file", b"certified or qualified"],
            "/support": [b"Support", b"/studio", b"/issues"],
        }
        for path, expected in pages.items():
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                for text in expected:
                    self.assertIn(text, response.data)

    @patch.dict(os.environ, {"SUPPORT_EMAIL": "help@example.com"})
    @patch("studio_mcp.RETENTION_DAYS", 3)
    def test_support_email_and_retention_come_from_the_settings(self):
        self.assertIn(b"mailto:help@example.com", self.client.get("/support").data)
        self.assertIn(b"deleted 3 days after your session ends", self.client.get("/privacy").data)

    def test_mcp_streamable_http_mount_exists(self):
        request_body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0.1"},
            },
        }
        with AsgiTestClient(asgi_app) as client:
            response = client.post(
                "/mcp/",
                json=request_body,
                headers={
                    "Accept": "application/json, text/event-stream",
                    "Host": "localhost",
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("artcraft-storytold", response.text)

    async def test_tools_list(self):
        async with Client(mcp) as client:
            result = await client.list_tools()
        self.assertEqual(
            {tool.name for tool in result.tools},
            {"list_artcraft_projects", "search_artcraft_projects", "get_artcraft_project"},
        )

    @patch("app.fetch_storytold_repos")
    async def test_list_projects_tool(self, fetch_repos):
        fetch_repos.return_value = [
            {
                "name": "artcraft",
                "description": "Creative image and video editor",
                "url": "https://github.com/storytold/artcraft",
                "language": "Rust",
                "stars": 100,
                "topics": ["creative"],
                "updated_at": "2026-10-01T00:00:00Z",
            }
        ]
        async with Client(mcp) as client:
            result = await client.call_tool("list_artcraft_projects", {"limit": 5})
        self.assertEqual(result.structured_content["projects"][0]["name"], "artcraft")

    @patch("app.fetch_storytold_repos")
    async def test_artcraft_leads_the_project_list(self, fetch_repos):
        fetch_repos.return_value = [
            {"name": name, "description": "Creative tool", "url": f"https://github.com/storytold/{name}",
             "language": "Rust", "stars": stars, "topics": [], "updated_at": None}
            for name, stars in (("photocraft", 900), ("artcraft", 100), ("pdfcraft", 500))
        ]
        async with Client(mcp) as client:
            result = await client.call_tool("list_artcraft_projects", {"limit": 5})
        names = [project["name"] for project in result.structured_content["projects"]]
        self.assertEqual(names, ["artcraft", "photocraft", "pdfcraft"])

    @patch("app.fetch_storytold_repos")
    async def test_search_scans_past_the_top_thirty_projects(self, fetch_repos):
        fetch_repos.return_value = [
            {
                "name": f"craft-{index:02}",
                "description": "Creative tool",
                "url": f"https://github.com/storytold/craft-{index:02}",
                "language": "Rust",
                "stars": 100 - index,
                "topics": ["creative"],
                "updated_at": "2026-10-01T00:00:00Z",
            }
            for index in range(30)
        ] + [
            {
                "name": "deep-artcraft-feature",
                "description": "rare target",
                "url": "https://github.com/storytold/deep-artcraft-feature",
                "language": "Rust",
                "stars": 0,
                "topics": ["creative"],
                "updated_at": "2026-10-01T00:00:00Z",
            }
        ]
        async with Client(mcp) as client:
            result = await client.call_tool(
                "search_artcraft_projects", {"query": "rare target", "limit": 2}
            )
        self.assertEqual(
            result.structured_content["projects"][0]["name"],
            "deep-artcraft-feature",
        )

    async def test_search_requires_a_query(self):
        async with Client(mcp) as client:
            result = await client.call_tool("search_artcraft_projects", {"query": " "})
        self.assertTrue(result.is_error)

    @patch("app.github_get", side_effect=GitHubRequestError("GitHub unavailable"))
    async def test_github_error_is_reported_as_tool_error(self, _github_get):
        async with Client(mcp) as client:
            result = await client.call_tool("get_artcraft_project", {"name": "artcraft"})
        self.assertTrue(result.is_error)
        self.assertIn("GitHub unavailable", result.content[0].text)

    @patch("app.github_get")
    async def test_get_project_returns_bounded_readme(self, github_get):
        github_get.side_effect = [
            {
                "name": "artcraft",
                "description": "Creative editor",
                "html_url": "https://github.com/storytold/artcraft",
                "language": "Rust",
                "stargazers_count": 100,
                "topics": ["creative"],
                "updated_at": "2026-10-01T00:00:00Z",
            },
            {
                "content": base64.b64encode(b"A" * 20_000).decode("ascii"),
                "encoding": "base64",
            },
        ]
        async with Client(mcp) as client:
            result = await client.call_tool("get_artcraft_project", {"name": "artcraft"})
        self.assertFalse(result.is_error)
        self.assertEqual(len(result.structured_content["readme"]), 12_000)
        self.assertTrue(result.structured_content["readme_truncated"])

    async def test_invalid_project_name_is_reported(self):
        async with Client(mcp) as client:
            result = await client.call_tool("get_artcraft_project", {"name": "../private"})
        self.assertTrue(result.is_error)

    async def test_local_image_server_registers_generation_and_status_tools(self):
        async with Client(local_mcp) as client:
            result = await client.list_tools()
        self.assertEqual(
            {tool.name for tool in result.tools},
            {
                "generate_artcraft_image",
                "generate_artcraft_video",
                "list_artcraft_models",
                "estimate_artcraft_cost",
                "get_artcraft_job",
                "get_artcraft_image_job",
                "list_artcraft_projects",
                "search_artcraft_projects",
                "get_artcraft_project",
                "inspect_pdf_document",
                "extract_pdf_text",
                "search_pdf_text",
                "combine_pdf_documents",
                "extract_pdf_pages",
                "rotate_pdf_pages",
                "list_photocraft_tools",
                "call_photocraft_tool",
            },
        )

    async def test_unified_local_server_uses_bridgecraft_icons(self):
        async with Client(local_mcp) as client:
            png, svg = client.server_info.icons
            server_info = client.server_info
        self.assertEqual(png.src, BRAND_PNG_URL)
        self.assertEqual(png.mime_type, "image/png")
        self.assertEqual(png.sizes, ["512x512"])
        self.assertEqual(svg.src, BRAND_ICON_URL)
        self.assertEqual(svg.mime_type, "image/svg+xml")
        self.assertEqual(server_info.title, "BridgeCraft")
        self.assertEqual(server_info.description, ARTCRAFT_APP_DESCRIPTION)
        self.assertEqual(server_info.website_url, ARTCRAFT_SITE_URL)

    async def test_unified_server_registers_pdfcraft_workflows(self):
        async with Client(local_mcp) as client:
            result = await client.list_tools()
        self.assertTrue(
            {
                "inspect_pdf_document",
                "extract_pdf_text",
                "search_pdf_text",
                "combine_pdf_documents",
                "extract_pdf_pages",
                "rotate_pdf_pages",
            }.issubset({tool.name for tool in result.tools})
        )

    def test_photocraft_parameters_confine_read_and_write_roots(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "ArtCraft"
            root.mkdir()
            cli_path = Path(temporary) / "photocraft-cli"
            cli_path.touch()
            cli_path.chmod(0o755)
            with (
                patch("craft_app_mcp.PHOTOCRAFT_ROOT", root),
                patch.dict(os.environ, {"PHOTOCRAFT_CLI_PATH": str(cli_path)}),
            ):
                parameters = _photocraft_parameters()

        self.assertEqual(parameters.command, str(cli_path))
        self.assertEqual(
            parameters.args,
            [
                "mcp",
                "--automation-read-root",
                str(root.resolve()),
                "--automation-write-root",
                str(root.resolve()),
            ],
        )
        self.assertEqual(parameters.cwd, str(root.resolve()))

    def test_photocraft_requires_an_existing_workspace_and_executable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "missing"
            cli_path = Path(temporary) / "photocraft-cli"
            cli_path.touch()
            cli_path.chmod(0o755)
            with (
                patch("craft_app_mcp.PHOTOCRAFT_ROOT", root),
                patch.dict(os.environ, {"PHOTOCRAFT_CLI_PATH": str(cli_path)}),
            ):
                with self.assertRaisesRegex(ToolError, "workspace does not exist"):
                    _photocraft_parameters()

            root.mkdir()
            cli_path.chmod(0o644)
            with (
                patch("craft_app_mcp.PHOTOCRAFT_ROOT", root),
                patch.dict(os.environ, {"PHOTOCRAFT_CLI_PATH": str(cli_path)}),
            ):
                with self.assertRaisesRegex(ToolError, "not found or not executable"):
                    _photocraft_parameters()

    async def test_photocraft_bridge_reuses_and_closes_its_mcp_session(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "ArtCraft"
            root.mkdir()
            cli_path = Path(temporary) / "photocraft-cli"
            cli_path.touch()
            cli_path.chmod(0o755)
            started_with = []
            closed = []
            sessions = []

            @asynccontextmanager
            async def fake_stdio_client(parameters):
                started_with.append(parameters)
                try:
                    yield object(), object()
                finally:
                    closed.append("stdio")

            class FakeSession:
                def __init__(self, _read_stream, _write_stream):
                    self.initialized = False
                    sessions.append(self)

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *_exc_info):
                    closed.append("session")

                async def initialize(self):
                    self.initialized = True

                async def list_tools(self):
                    return SimpleNamespace(tools=[])

            bridge = PhotoCraftBridge()
            with (
                patch("craft_app_mcp.PHOTOCRAFT_ROOT", root),
                patch.dict(os.environ, {"PHOTOCRAFT_CLI_PATH": str(cli_path)}),
                patch("craft_app_mcp.stdio_client", fake_stdio_client),
                patch("craft_app_mcp.ClientSession", FakeSession),
            ):
                first = await bridge.list_tools()
                second = await bridge.list_tools()
                await bridge.close()

            self.assertEqual(first.tools, [])
            self.assertEqual(second.tools, [])
            self.assertEqual(len(sessions), 1)
            self.assertTrue(sessions[0].initialized)
            self.assertEqual(len(started_with), 1)
            self.assertEqual(closed, ["session", "stdio"])
            self.assertEqual(
                started_with[0].args[2],
                str(root.resolve()),
            )

    async def test_photocraft_native_tools_are_discovered_and_forwarded(self):
        native_tool = Tool(
            name="doc_inspect",
            inputSchema={
                "type": "object",
                "properties": {"doc": {"type": "integer"}},
                "required": ["doc"],
            },
        )
        native_result = CallToolResult(
            content=[TextContent(type="text", text="native result")],
            structuredContent={"pages": 2},
        )
        list_tools = AsyncMock(
            return_value=SimpleNamespace(tools=[native_tool])
        )
        call_tool = AsyncMock(return_value=native_result)
        with (
            patch("craft_app_mcp._photocraft_bridge.list_tools", new=list_tools),
            patch("craft_app_mcp._photocraft_bridge.call_tool", new=call_tool),
        ):
            async with Client(local_mcp) as client:
                catalog = await client.call_tool("list_photocraft_tools", {})
                forwarded = await client.call_tool(
                    "call_photocraft_tool",
                    {"tool_name": "doc_inspect", "arguments": {"doc": 7}},
                )

        self.assertFalse(catalog.is_error)
        self.assertEqual(
            catalog.structured_content["tools"][0]["inputSchema"]["required"],
            ["doc"],
        )
        self.assertFalse(forwarded.is_error)
        self.assertEqual(forwarded.structured_content, {"pages": 2})
        self.assertEqual(forwarded.content[0].text, "native result")
        call_tool.assert_awaited_once_with("doc_inspect", {"doc": 7})

    async def test_photocraft_proxy_rejects_unknown_native_tool_names(self):
        list_tools = AsyncMock(return_value=SimpleNamespace(tools=[]))
        call_tool = AsyncMock()
        with patch(
            "craft_app_mcp._photocraft_bridge.list_tools",
            new=list_tools,
        ):
            with patch(
                "craft_app_mcp._photocraft_bridge.call_tool",
                new=call_tool,
            ):
                async with Client(local_mcp) as client:
                    result = await client.call_tool(
                        "call_photocraft_tool",
                        {"tool_name": "doc_delete_everything", "arguments": {}},
                    )

        self.assertTrue(result.is_error)
        self.assertIn("Unknown PhotoCraft tool", result.content[0].text)
        call_tool.assert_not_awaited()

    def test_pdf_paths_are_confined_to_documents_and_outputs_are_new(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "Documents"
            root.mkdir()
            source = root / "source.pdf"
            source.write_bytes(b"%PDF-test")
            outside = base / "outside.pdf"
            outside.write_bytes(b"%PDF-test")
            (root / "escape.pdf").symlink_to(outside)

            with patch("pdfcraft_mcp.PDFCRAFT_ROOT", root):
                self.assertEqual(_pdf_path("source.pdf"), source.resolve())
                with self.assertRaises(ToolError):
                    _pdf_path("../outside.pdf")
                with self.assertRaises(ToolError):
                    _pdf_path("escape.pdf")
                with self.assertRaises(ToolError):
                    _pdf_path("source.pdf", output=True)

    async def test_pdf_text_extraction_keeps_open_document_session(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "source.pdf").write_bytes(b"%PDF-test")
            calls = []

            class FakeSession:
                async def call_tool(self, name, arguments):
                    calls.append((name, arguments))
                    response = (
                        {"doc": 41}
                        if name == "doc_open"
                        else {"text": "sample text"}
                    )
                    return SimpleNamespace(
                        is_error=False,
                        structured_content=response,
                        content=[],
                    )

            async def run_workflow(workflow):
                result, error = await workflow(FakeSession())
                if error:
                    raise ToolError(error)
                return result

            with (
                patch("pdfcraft_mcp.PDFCRAFT_ROOT", root),
                patch("pdfcraft_mcp._run_pdfcraft", side_effect=run_workflow),
            ):
                async with Client(local_mcp) as client:
                    result = await client.call_tool(
                        "extract_pdf_text",
                        {"path": "source.pdf", "pages": [1]},
                    )

            self.assertFalse(result.is_error)
            self.assertEqual(result.structured_content["data"]["text"], "sample text")
            self.assertEqual(
                calls,
                [
                    ("doc_open", {"path": str((root / "source.pdf").resolve())}),
                    ("text_extract", {"doc": 41, "pages": [1]}),
                ],
            )

    async def test_pdf_rotation_saves_to_a_new_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "source.pdf").write_bytes(b"%PDF-test")
            calls = []

            class FakeSession:
                async def call_tool(self, name, arguments):
                    calls.append((name, arguments))
                    response = {"doc": 9} if name == "doc_open" else {"saved": True}
                    return SimpleNamespace(
                        is_error=False,
                        structured_content=response,
                        content=[],
                    )

            async def run_workflow(workflow):
                result, error = await workflow(FakeSession())
                if error:
                    raise ToolError(error)
                return result

            with (
                patch("pdfcraft_mcp.PDFCRAFT_ROOT", root),
                patch("pdfcraft_mcp._run_pdfcraft", side_effect=run_workflow),
            ):
                async with Client(local_mcp) as client:
                    result = await client.call_tool(
                        "rotate_pdf_pages",
                        {
                            "path": "source.pdf",
                            "pages": [1],
                            "degrees": 90,
                            "output_path": "rotated.pdf",
                        },
                    )

            self.assertFalse(result.is_error)
            self.assertEqual(
                result.structured_content["path"],
                str((root / "rotated.pdf").resolve()),
            )
            self.assertEqual(
                [name for name, _ in calls],
                ["doc_open", "page_rotate", "doc_save"],
            )
            self.assertEqual(
                calls[-1][1]["path"],
                str((root / "rotated.pdf").resolve()),
            )

    @patch(
        "local_artcraft_mcp.list_projects",
        return_value=[
            {
                "name": "filmcraft",
                "description": "Video editor",
                "url": "https://github.com/storytold/filmcraft",
                "language": "Rust",
                "stars": 12,
                "topics": ["creative"],
                "updated_at": "2026-10-01T00:00:00Z",
            }
        ],
    )
    async def test_unified_server_lists_storytold_apps(self, list_projects):
        async with Client(local_mcp) as client:
            result = await client.call_tool("list_artcraft_projects", {"limit": 5})
        self.assertFalse(result.is_error)
        self.assertEqual(result.structured_content["projects"][0]["name"], "filmcraft")
        list_projects.assert_called_once_with(5)

    @patch(
        "local_artcraft_mcp.submit_image_generation",
        return_value={
            "job_token": "jinf_test123",
            "model": "flux_1_schnell",
            "status": "pending",
            "credits": 0,
            "message": "Accepted.",
        },
    )
    async def test_local_generation_tool_submits_image_job(self, submit_image):
        async with Client(local_mcp) as client:
            result = await client.call_tool(
                "generate_artcraft_image",
                {
                    "prompt": "A watercolor fox in a garden",
                    "model": "flux_1_schnell",
                },
            )
        self.assertFalse(result.is_error)
        self.assertEqual(result.structured_content["job_token"], "jinf_test123")
        submit_image.assert_called_once_with(
            "A watercolor fox in a garden", "flux_1_schnell", 1, None, None
        )

    @patch(
        "local_artcraft_mcp.submit_image_generation",
        side_effect=ArtCraftAPIError("No ArtCraft API key was found in Keychain."),
    )
    async def test_local_generation_tool_reports_missing_api_key(self, _submit_image):
        async with Client(local_mcp) as client:
            result = await client.call_tool(
                "generate_artcraft_image",
                {"prompt": "A watercolor fox", "model": "flux_1_schnell"},
            )
        self.assertTrue(result.is_error)
        self.assertIn("No ArtCraft API key", result.content[0].text)

    @patch.dict(os.environ, {"ARTCRAFT_API_KEY": ""})
    @patch("artcraft_api.sys.platform", "darwin")
    @patch("artcraft_api._keyring_password", return_value=None)
    @patch("artcraft_api.subprocess.run")
    def test_api_key_falls_back_to_legacy_keychain_item(self, run, _keyring_password):
        run.side_effect = [
            subprocess.CalledProcessError(44, "security"),
            SimpleNamespace(stdout="legacy-key\n"),
        ]
        self.assertEqual(get_api_key(), "legacy-key")
        services = [call.args[0][call.args[0].index("-s") + 1] for call in run.call_args_list]
        self.assertEqual(services, ["artcraft-plugin", "artcraft-chatgpt-mcp"])

    @patch("artcraft_api.get_api_key", return_value="artcraft_api_test-secret")
    @patch("artcraft_api.request.build_opener")
    def test_image_request_uses_artcraft_auth_idempotency_and_bounded_batch(
        self, build_opener, _get_api_key
    ):
        from artcraft_api import submit_image_generation

        build_opener.return_value.open.return_value.__enter__.return_value.read.side_effect = [
            FREE_ESTIMATE,
            b'{"success":true,"inference_job_token":"jinf_test123"}',
        ]
        result = submit_image_generation(
            " A watercolor fox ", "flux_1_schnell", image_batch_count=2
        )
        estimate_request, api_request = [
            call.args[0] for call in build_opener.return_value.open.call_args_list
        ]
        self.assertEqual(
            estimate_request.full_url, "https://api.storyteller.ai/v1/generate/cost_estimate/image"
        )
        self.assertEqual(
            json.loads(estimate_request.data)["generation_mode"], {"type": "text_to_image"}
        )
        payload = json.loads(api_request.data)
        self.assertEqual(
            api_request.get_header("Authorization"),
            "Bearer artcraft_api_test-secret",
        )
        # Cloudflare in front of the API refuses Python's default user agent.
        self.assertTrue(api_request.get_header("User-agent").startswith("ArtCraft-Plugin/"))
        self.assertEqual(api_request.full_url, "https://api.storyteller.ai/v1/omni_api/generate/image")
        self.assertEqual(payload["prompt"], "A watercolor fox")
        self.assertEqual(payload["model"], "flux_1_schnell")
        self.assertEqual(payload["image_batch_count"], 2)
        self.assertRegex(payload["idempotency_token"], r"^[0-9a-f-]{36}$")
        self.assertEqual(result["job_token"], "jinf_test123")

    @patch("artcraft_api.get_api_key", return_value="artcraft_api_test-secret")
    @patch("artcraft_api.request.build_opener")
    def test_image_job_status_returns_result_url(self, build_opener, _get_api_key):
        from artcraft_api import get_image_job

        build_opener.return_value.open.return_value.__enter__.return_value.read.return_value = (
            b'{"success":true,"state":{"status":{"status":"complete_success",'
            b'"progress_percentage":100,"maybe_failure_category":null},'
            b'"maybe_result":{"media_links":{"cdn_url":"https://cdn.example/image.jpg"}}}}'
        )
        result = get_image_job("jinf_test123")
        self.assertEqual(result["status"], "complete_success")
        self.assertEqual(result["image_url"], "https://cdn.example/image.jpg")
        self.assertEqual(
            build_opener.return_value.open.call_args.args[0].full_url,
            "https://api.storyteller.ai/v1/omni_api/job_status/job/jinf_test123",
        )

    @patch("artcraft_api.get_api_key", return_value="artcraft_api_test-secret")
    @patch("artcraft_api.request.build_opener")
    def test_paid_generation_waits_for_the_users_agreed_credits(self, build_opener, _get_api_key):
        from artcraft_api import submit_image_generation

        read = build_opener.return_value.open.return_value.__enter__.return_value.read
        read.side_effect = [PAID_ESTIMATE]
        with self.assertRaisesRegex(ArtCraftAPIError, "cost 6 ArtCraft credits.*max_credits=6"):
            submit_image_generation("A fox", "flux_pro_1p1_ultra")
        read.side_effect = [PAID_ESTIMATE]
        with self.assertRaisesRegex(ArtCraftAPIError, "max_credits=6"):
            submit_image_generation("A fox", "flux_pro_1p1_ultra", max_credits=5)
        # Only the two estimates went out; nothing was generated.
        urls = [call.args[0].full_url for call in build_opener.return_value.open.call_args_list]
        self.assertTrue(all("cost_estimate" in url for url in urls))

        read.side_effect = [PAID_ESTIMATE, b'{"success":true,"inference_job_token":"jinf_paid"}']
        result = submit_image_generation("A fox", "flux_pro_1p1_ultra", max_credits=6)
        self.assertEqual((result["job_token"], result["credits"]), ("jinf_paid", 6))

    @patch("artcraft_api.get_api_key", return_value="artcraft_api_test-secret")
    @patch("artcraft_api.request.build_opener")
    def test_video_request_estimates_then_submits_the_options(self, build_opener, _get_api_key):
        from artcraft_api import submit_video_generation

        build_opener.return_value.open.return_value.__enter__.return_value.read.side_effect = [
            PAID_ESTIMATE,
            b'{"success":true,"inference_job_token":"jinf_video1"}',
        ]
        result = submit_video_generation(
            "A paper boat on a river", "wan_3p0", aspect_ratio="wide_sixteen_by_nine",
            duration_seconds=5, start_frame_image_url="https://cdn.example/boat.png", max_credits=10,
        )
        estimate_request, video_request = [
            call.args[0] for call in build_opener.return_value.open.call_args_list
        ]
        self.assertEqual(
            json.loads(estimate_request.data)["generation_mode"], {"type": "start_frame_to_video"}
        )
        self.assertIsNone(estimate_request.get_header("Authorization"))
        self.assertEqual(video_request.full_url, "https://api.storyteller.ai/v1/omni_api/generate/video")
        payload = json.loads(video_request.data)
        self.assertEqual(payload["duration_seconds"], 5)
        self.assertEqual(payload["start_frame_image_url"], "https://cdn.example/boat.png")
        self.assertNotIn("resolution", payload)
        self.assertEqual(result["job_token"], "jinf_video1")

    def test_video_request_validates_before_spending(self):
        from artcraft_api import submit_video_generation

        with self.assertRaisesRegex(ArtCraftAPIError, "https://"):
            submit_video_generation("A boat", "wan_3p0", start_frame_image_url="file:///etc/passwd")
        with self.assertRaisesRegex(ArtCraftAPIError, "duration_seconds"):
            submit_video_generation("A boat", "wan_3p0", duration_seconds=600)
        with self.assertRaisesRegex(ArtCraftAPIError, "option names"):
            submit_video_generation("A boat", "wan_3p0", aspect_ratio="16:9")

    @patch("artcraft_api.request.build_opener")
    def test_model_list_marks_models_usable_with_an_api_key(self, build_opener):
        from artcraft_api import list_models

        build_opener.return_value.open.return_value.__enter__.return_value.read.return_value = (
            b'{"success":true,"models":[{"model":"wan_3p0","full_name":"Wan 3.0",'
            b'"aspect_ratio_options":["square"],"duration_seconds_max":10},'
            b'{"model":"sora_2","full_name":"Sora 2"}],'
            b'"providers":[{"provider":"artcraft","models":[{"model":"wan_3p0"}]}]}'
        )
        result = list_models("video")
        self.assertEqual(
            [(m["model"], m["available_with_api_key"]) for m in result["models"]],
            [("wan_3p0", True), ("sora_2", False)],
        )
        self.assertEqual(result["models"][0]["max_seconds"], 10)
        request_sent = build_opener.return_value.open.call_args.args[0]
        self.assertEqual(request_sent.full_url, "https://api.storyteller.ai/v1/omni_gen/models/video")
        self.assertIsNone(request_sent.get_header("Authorization"))
        with self.assertRaisesRegex(ArtCraftAPIError, "kind must be"):
            list_models("mesh")

    async def test_local_server_exposes_cost_estimates(self):
        with patch("local_artcraft_mcp.estimate_cost", return_value={
            "model": "flux_pro_1p1_ultra", "is_free": False, "credits": 6, "usd_cents": 6,
            "is_rate_limited": False, "has_watermark": False,
        }) as estimate:
            async with Client(local_mcp) as client:
                result = await client.call_tool(
                    "estimate_artcraft_cost", {"kind": "image", "model": "flux_pro_1p1_ultra"}
                )
        self.assertFalse(result.is_error)
        self.assertEqual(result.structured_content["credits"], 6)
        self.assertEqual(estimate.call_args.args, ("image", "flux_pro_1p1_ultra"))

    def test_generation_rejects_batch_above_limit_before_request(self):
        from artcraft_api import submit_image_generation

        with self.assertRaisesRegex(ArtCraftAPIError, "between 1 and 4"):
            submit_image_generation("A watercolor fox", "flux_1_schnell", 5)

    @patch("artcraft_api.get_api_key", return_value="artcraft_api_test-secret")
    @patch("artcraft_api.request.build_opener")
    def test_insufficient_credits_is_an_explicit_error(
        self, build_opener, _get_api_key
    ):
        from artcraft_api import _request_json
        from urllib.error import HTTPError
        from io import BytesIO

        build_opener.return_value.open.side_effect = HTTPError(
            "https://api.storyteller.ai/v1/omni_api/generate/image",
            402,
            "Payment Required",
            {},
            BytesIO(b""),
        )
        with self.assertRaisesRegex(ArtCraftAPIError, "insufficient credits"):
            _request_json("/v1/omni_api/generate/image", "POST", {})

    @patch("artcraft_api.get_api_key", return_value="artcraft_api_test-secret")
    @patch("artcraft_api.request.build_opener")
    def test_http_redirect_is_not_followed(self, build_opener, _get_api_key):
        from artcraft_api import _request_json
        from urllib.error import HTTPError
        from io import BytesIO

        build_opener.return_value.open.side_effect = HTTPError(
            "https://api.storyteller.ai/v1/omni_api/job_status/job/jinf_test123",
            302,
            "Found",
            {"Location": "https://attacker.example/collect"},
            BytesIO(b""),
        )
        with self.assertRaisesRegex(ArtCraftAPIError, "unexpected redirect"):
            _request_json("/v1/omni_api/job_status/job/jinf_test123", "GET")


if __name__ == "__main__":
    unittest.main()
