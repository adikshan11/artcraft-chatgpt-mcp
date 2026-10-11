import base64
import json
import os
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from mcp import Client
from mcp.types import CallToolResult, TextContent, Tool
from mcp.server.mcpserver.exceptions import ToolError
from starlette.testclient import TestClient as AsgiTestClient

from app import (
    ARTCRAFT_APP_DESCRIPTION,
    ARTCRAFT_ICON_URL,
    ARTCRAFT_PRODUCT_URL,
    ARTCRAFT_SITE_URL,
    GitHubRequestError,
    app,
    asgi_app,
    mcp,
    mcp_allowed_hosts,
)
from artcraft_api import ArtCraftAPIError
from craft_app_mcp import PhotoCraftBridge, _photocraft_parameters
from local_artcraft_mcp import ARTCRAFT_ICON_URL, server as local_mcp
from pdfcraft_mcp import _pdf_path


class McpServerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        app.config.update(TESTING=True)
        self.client = app.test_client()

    def test_health_endpoint(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"status": "ok", "mcp_endpoint": "/mcp/", "studio_endpoint": "/studio"})

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
        self.assertIn(b"Creative work,", response.data)
        self.assertIn(b"OPEN-SOURCE COMMUNITY INTEGRATION", response.data)
        self.assertIn(b"One connection. Useful workflows.", response.data)
        self.assertIn(b"ONE ARTCRAFT APP", response.data)
        self.assertIn(b"artcraft-chatgpt-mcp", response.data)
        self.assertIn(b"PdfCraft workflows", response.data)
        self.assertIn(b"PhotoCraft tools", response.data)
        self.assertIn(b"ArtCraft image generation", response.data)
        self.assertIn(ARTCRAFT_ICON_URL.encode(), response.data)
        self.assertIn(
            f'href="{ARTCRAFT_PRODUCT_URL}"'.encode(),
            response.data,
        )
        self.assertIn(ARTCRAFT_PRODUCT_URL.encode(), ARTCRAFT_APP_DESCRIPTION.encode())
        self.assertNotIn(b"Adobe Express", response.data)

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

    async def test_unified_local_server_uses_official_artcraft_icon(self):
        async with Client(local_mcp) as client:
            icon = client.server_info.icons[0]
            server_info = client.server_info
        self.assertEqual(icon.src, ARTCRAFT_ICON_URL)
        self.assertEqual(icon.mime_type, "image/svg+xml")
        self.assertEqual(server_info.title, "ArtCraft")
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
            "A watercolor fox in a garden", "flux_1_schnell", 1
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

    @patch("artcraft_api.get_api_key", return_value="artcraft_api_test-secret")
    @patch("artcraft_api.request.build_opener")
    def test_image_request_uses_artcraft_auth_idempotency_and_bounded_batch(
        self, build_opener, _get_api_key
    ):
        from artcraft_api import submit_image_generation

        build_opener.return_value.open.return_value.__enter__.return_value.read.return_value = (
            b'{"success":true,"inference_job_token":"jinf_test123"}'
        )
        result = submit_image_generation(
            " A watercolor fox ", "flux_1_schnell", image_batch_count=2
        )
        api_request = build_opener.return_value.open.call_args.args[0]
        payload = json.loads(api_request.data)
        self.assertEqual(
            api_request.get_header("Authorization"),
            "Bearer artcraft_api_test-secret",
        )
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
