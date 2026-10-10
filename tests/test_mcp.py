import base64
import unittest
from unittest.mock import patch

from mcp import Client
from starlette.testclient import TestClient as AsgiTestClient

from app import GitHubRequestError, app, asgi_app, mcp, mcp_allowed_hosts


class McpServerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        app.config.update(TESTING=True)
        self.client = app.test_client()

    def test_health_endpoint(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"status": "ok", "mcp_endpoint": "/mcp/"})

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
    def test_homepage_describes_real_mcp_tools(self, _list_projects):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"search_artcraft_projects", response.data)
        self.assertIn(b"/mcp/", response.data)
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


if __name__ == "__main__":
    unittest.main()
