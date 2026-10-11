import base64
import json
import os
import re
from contextlib import asynccontextmanager
from functools import lru_cache
from urllib import error, request

from a2wsgi import WSGIMiddleware
from flask import Flask, jsonify, render_template
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import Icon, ToolAnnotations
from pydantic import BaseModel
from starlette.applications import Starlette
from starlette.routing import Mount

app = Flask(__name__)

ORG_NAME = "storytold"
ORG_URL = f"https://github.com/{ORG_NAME}"
PROJECT_URL = "https://github.com/adikshan11/artcraft-chatgpt-mcp"
ARTCRAFT_SITE_URL = "https://artcraft-storytold-mcp.onrender.com"
ARTCRAFT_PRODUCT_URL = "https://getartcraft.com"
ARTCRAFT_ICON_URL = (
    "https://raw.githubusercontent.com/storytold/artcraft/main/"
    "frontend/apps/artcraft/app/public/artcraft-icon.svg"
)
ARTCRAFT_APP_DESCRIPTION = (
    "A community-built ChatGPT connection for ArtCraft image generation, public "
    "Storytold projects, local PDF workflows, and PhotoCraft tools. Image generation "
    "may use credits; some other Craft app controls are not yet available. Project "
    f"website: {ARTCRAFT_SITE_URL}. Official ArtCraft: {ARTCRAFT_PRODUCT_URL}"
)
GITHUB_API = "https://api.github.com"
MAX_README_CHARS = 12000
MCP_INSTRUCTIONS = (
    "Use these read-only tools to discover public ArtCraft and Storytold projects. "
    "They search, list, and read repository information only; they do not control "
    "the ArtCraft desktop editor or create or edit media."
)
mcp = MCPServer(
    "artcraft-storytold",
    title="ArtCraft Project Catalog",
    description="Browse public Storytold creative apps and open-source project information.",
    website_url=ARTCRAFT_SITE_URL,
    icons=[Icon(src=ARTCRAFT_ICON_URL, mimeType="image/svg+xml")],
    version="0.1.0",
    instructions=MCP_INSTRUCTIONS,
)


class ProjectSummary(BaseModel):
    name: str
    description: str
    url: str
    language: str | None
    stars: int
    topics: list[str]
    updated_at: str | None


class ProjectList(BaseModel):
    projects: list[ProjectSummary]


class ProjectDetails(ProjectSummary):
    readme: str
    readme_truncated: bool


class GitHubRequestError(RuntimeError):
    """A GitHub API request failed or returned an invalid response."""


def github_get(path):
    token = os.environ.get("GITHUB_TOKEN")
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "ArtCraft-ChatGPT-MCP/1.0",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = request.Request(f"{GITHUB_API}{path}", headers=headers)
    try:
        with request.urlopen(req, timeout=20) as response:
            return json.load(response)
    except error.HTTPError as exc:
        if exc.code == 404:
            raise GitHubRequestError("No matching public Storytold repository was found.") from exc
        if exc.code == 403:
            raise GitHubRequestError(
                "GitHub API rate limit or permission error. Set GITHUB_TOKEN for a higher rate limit."
            ) from exc
        raise GitHubRequestError(f"GitHub API request failed with HTTP {exc.code}.") from exc
    except (error.URLError, TimeoutError) as exc:
        raise GitHubRequestError("Could not reach the GitHub API.") from exc
    except json.JSONDecodeError as exc:
        raise GitHubRequestError("GitHub returned an invalid JSON response.") from exc


def normalize_repo(repo):
    return {
        "name": repo["name"],
        "description": repo.get("description") or "No description provided.",
        "url": repo["html_url"],
        "language": repo.get("language"),
        "stars": repo.get("stargazers_count", 0),
        "topics": repo.get("topics", []),
        "updated_at": repo.get("updated_at"),
    }


@lru_cache(maxsize=1)
def fetch_storytold_repos():
    repos = github_get(f"/orgs/{ORG_NAME}/repos?per_page=100&sort=updated")
    if not isinstance(repos, list):
        raise GitHubRequestError("GitHub returned an unexpected repository list.")
    return [normalize_repo(repo) for repo in repos if isinstance(repo, dict) and repo.get("name")]


def relevant_repo(repo):
    searchable = " ".join(
        [
            repo["name"],
            repo["description"],
            " ".join(repo.get("topics", [])),
            str(repo.get("language") or ""),
        ]
    ).lower()
    terms = (
        "artcraft", "craft", "creative", "image", "video", "film", "design",
        "3d", "model", "audio", "photo", "vector", "story", "ai",
    )
    return any(term in searchable for term in terms)


def relevant_projects():
    repos = [repo for repo in fetch_storytold_repos() if relevant_repo(repo)]
    return sorted(repos, key=lambda repo: (-repo["stars"], repo["name"].lower()))


def list_projects(limit=15):
    limit = max(1, min(int(limit), 30))
    return relevant_projects()[:limit]


def search_projects(query, limit=10):
    query = query.strip().lower()
    if not query:
        raise ValueError("Provide a non-empty query.")
    limit = max(1, min(int(limit), 30))
    return [
        repo
        for repo in relevant_projects()
        if query in repo["name"].lower() or query in repo["description"].lower()
    ][:limit]


def get_project_details(name):
    project_name = name.strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", project_name):
        raise ValueError("Provide a valid repository name.")
    repo = github_get(f"/repos/{ORG_NAME}/{project_name}")
    data = normalize_repo(repo)
    readme = github_get(f"/repos/{ORG_NAME}/{project_name}/readme")
    if isinstance(readme, dict) and readme.get("content"):
        full_readme = base64.b64decode(readme["content"]).decode("utf-8", errors="replace")
        data["readme"] = full_readme[:MAX_README_CHARS]
        data["readme_truncated"] = len(full_readme) > MAX_README_CHARS
    else:
        data["readme"] = ""
        data["readme_truncated"] = False
    return ProjectDetails(**data)


@mcp.tool(
    title="List ArtCraft projects",
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    structured_output=True,
)
def list_artcraft_projects(limit: int = 15) -> ProjectList:
    """List public Storytold repositories relevant to ArtCraft, creative work, AI, design, or filmmaking."""
    try:
        return ProjectList(projects=list_projects(limit))
    except GitHubRequestError as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(
    title="Search ArtCraft projects",
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    structured_output=True,
)
def search_artcraft_projects(query: str, limit: int = 10) -> ProjectList:
    """Search public Storytold repositories by project name or description."""
    try:
        return ProjectList(projects=search_projects(query, limit))
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    except GitHubRequestError as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(
    title="Get ArtCraft project details",
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    structured_output=True,
)
def get_artcraft_project(name: str) -> ProjectDetails:
    """Get public details and the README for one Storytold project such as artcraft or photocraft."""
    try:
        return get_project_details(name)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    except GitHubRequestError as exc:
        raise ToolError(str(exc)) from exc


@app.get("/health")
def health():
    return jsonify({"status": "ok", "mcp_endpoint": "/mcp/", "studio_endpoint": "/studio"})


@app.get("/")
def index():
    try:
        repos = list_projects(12)
        error_message = None
    except GitHubRequestError as exc:
        repos = []
        error_message = str(exc)
    return render_template(
        "index.html",
        org_name=ORG_NAME,
        org_url=ORG_URL,
        project_url=PROJECT_URL,
        product_url=ARTCRAFT_PRODUCT_URL,
        site_url=ARTCRAFT_SITE_URL,
        icon_url=ARTCRAFT_ICON_URL,
        repos=repos,
        error_message=error_message,
    )


def mcp_allowed_hosts():
    configured_hosts = os.environ.get("MCP_ALLOWED_HOSTS")
    if configured_hosts:
        return [host.strip() for host in configured_hosts.split(",") if host.strip()]
    if render_hostname := os.environ.get("RENDER_EXTERNAL_HOSTNAME"):
        return [render_hostname, f"{render_hostname}:*"]
    return ["localhost", "localhost:*", "127.0.0.1", "127.0.0.1:*", "[::1]", "[::1]:*"]


def create_asgi_app():
    mcp_app = mcp.streamable_http_app(
        streamable_http_path="/",
        transport_security=TransportSecuritySettings(allowed_hosts=mcp_allowed_hosts()),
    )

    from studio_mcp import STUDIO_PATH, create_studio_app, studio

    studio_app = create_studio_app(mcp_allowed_hosts())

    @asynccontextmanager
    async def lifespan(_app):
        async with mcp.session_manager.run(), studio.session_manager.run():
            yield

    site = Starlette(
        routes=[
            Mount("/mcp", app=mcp_app),
            Mount("/", app=WSGIMiddleware(app)),
        ],
        lifespan=lifespan,
    )
    # The Studio app serves its own OAuth protected-resource metadata at the site
    # root, so it is dispatched by path instead of being mounted under a prefix.
    studio_prefixes = (STUDIO_PATH, "/.well-known/oauth-protected-resource")

    async def dispatch(scope, receive, send):
        path = scope.get("path", "")
        if scope["type"] in ("http", "websocket") and path.startswith(studio_prefixes):
            await studio_app(scope, receive, send)
        else:
            await site(scope, receive, send)

    return dispatch


asgi_app = create_asgi_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(asgi_app, host="0.0.0.0", port=int(os.environ.get("PORT", "5001")))
