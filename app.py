# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Adithya Shankaran

import asyncio
import base64
import hashlib
import json
import os
import re
import time

import log_redaction

log_redaction.install()
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from urllib import error, request

from a2wsgi import WSGIMiddleware
from flask import Flask, jsonify, redirect, render_template, send_from_directory
from flask import request as http_request
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import Icon, ToolAnnotations
from pydantic import BaseModel
from starlette.applications import Starlette
from starlette.routing import Mount

app = Flask(__name__)
STATIC_MAX_AGE = 365 * 24 * 3600


@lru_cache(maxsize=None)
def _static_version(filename):
    """A short hash of a static file's bytes; it changes whenever the file does."""
    try:
        data = (Path(app.static_folder) / filename).read_bytes()
    except OSError:
        return None
    return hashlib.sha256(data).hexdigest()[:10]


@app.url_defaults
def _version_static_urls(endpoint, values):
    # Static links carry ?v=<content hash>, so browsers can keep the file and still
    # get a new one the moment it changes. Fonts are left out: style.css loads them
    # by plain path, and their preload links must match that path exactly.
    if endpoint == "static" and "v" not in values and not values.get("filename", "").startswith("fonts/"):
        version = _static_version(values.get("filename", ""))
        if version:
            values["v"] = version


@app.after_request
def _cache_versioned_static(response):
    # A versioned link names one exact file, so it can be cached for a year without
    # asking again. Unversioned links (the connector icons other apps fetch) keep
    # Flask's default, which revalidates.
    if http_request.endpoint == "static" and response.status_code == 200:
        version = http_request.args.get("v")
        if version and version == _static_version(http_request.view_args.get("filename", "")):
            response.headers["Cache-Control"] = f"public, max-age={STATIC_MAX_AGE}, immutable"
    return response


@app.context_processor
def shared_links():
    # Cursor's install link carries the server's mcp.json entry as base64 JSON.
    studio_config = base64.b64encode(json.dumps({"url": f"{ARTCRAFT_SITE_URL}/studio"}).encode()).decode()
    return {
        "discord_url": DISCORD_URL,
        "cursor_install_url": f"cursor://anysphere.cursor-deeplink/mcp/install?name=bridgecraft&config={studio_config}",
    }

ORG_NAME = "storytold"
ORG_URL = f"https://github.com/{ORG_NAME}"
PROJECT_URL = "https://github.com/adikshan11/bridgecraft"
ARTCRAFT_SITE_URL = "https://bridgecraft.onrender.com"
ARTCRAFT_PRODUCT_URL = "https://getartcraft.com"
# Our own mark. The ArtCraft name and logos are the ArtCraft Team's trademarks.
BRAND_ICON_URL = f"{ARTCRAFT_SITE_URL}/static/bridgecraft.svg"
BRAND_PNG_URL = f"{ARTCRAFT_SITE_URL}/static/bridgecraft-512.png"
ARTCRAFT_APP_DESCRIPTION = (
    "BridgeCraft, a community-built MCP plugin for ArtCraft image and video generation, public "
    "Storytold projects, local PDF workflows, and PhotoCraft tools. Paid generations "
    "start only after you agree to the price; some other Craft app controls are not yet available. Project "
    f"website: {ARTCRAFT_SITE_URL}. Official ArtCraft: {ARTCRAFT_PRODUCT_URL}"
)
GITHUB_API = "https://api.github.com"
MAX_README_CHARS = 12000
# The Craft apps Studio hosts, in the order the site shows them.
HOSTED_APPS = [
    {"name": app["name"], "title": app["title"], "version": app["version"]}
    for app in json.loads((Path(__file__).parent / "craft_apps.json").read_text())["apps"]
]
# Each project's own icon, keyed by repository name (credited in NOTICE).
APP_ICONS = {
    path.stem: f"app-icons/{path.name}"
    for path in (Path(__file__).parent / "static" / "app-icons").iterdir()
    if path.suffix in (".png", ".svg")
}
# The ArtCraft community server, where I post as a contributor.
DISCORD_URL = "https://discord.gg/artcraft"
LEGAL_LAST_UPDATED = "1 November 2026"
# When this process started, for the uptime on /status.
STARTED_AT = time.time()
MCP_INSTRUCTIONS = (
    "Use these read-only tools to discover public ArtCraft and Storytold projects. "
    "They search, list, and read repository information only; they do not control "
    "the ArtCraft desktop editor or create or edit media."
)
mcp = MCPServer(
    "artcraft-storytold",
    title="BridgeCraft Project Catalog",
    description="Browse public Storytold creative apps and open-source project information.",
    website_url=ARTCRAFT_SITE_URL,
    icons=[
        Icon(src=BRAND_PNG_URL, mimeType="image/png", sizes=["512x512"]),
        Icon(src=BRAND_ICON_URL, mimeType="image/svg+xml"),
    ],
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
    from connector_auth import client_token_in_context
    client = client_token_in_context()
    if token and client and token == client:
        raise GitHubRequestError("Refusing to send the connector token to GitHub.")
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "BridgeCraft/1.0",
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
    # ArtCraft leads: the Craft apps are part of it.
    return sorted(repos, key=lambda repo: (repo["name"] != "artcraft", -repo["stars"], repo["name"].lower()))


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


@app.get("/favicon.ico")
@app.get("/apple-touch-icon.png")
@app.get("/apple-touch-icon-precomposed.png")
def root_icons():
    # Safari, and apps that fetch a page's icon, ask for these fixed paths and skip
    # the <link> tags; without them Safari shows a letter for the domain instead.
    name = "favicon.ico" if http_request.path == "/favicon.ico" else "apple-touch-icon.png"
    return send_from_directory(app.static_folder, name, max_age=24 * 3600)


@app.get("/health")
def health():
    # Render's health check and scripts get the JSON; a person opening it in a
    # browser gets the status page instead.
    if http_request.accept_mimetypes.best_match(["application/json", "text/html"]) == "text/html":
        return redirect("/status")
    return jsonify({"status": "ok", "mcp_endpoint": "/mcp/", "studio_endpoint": "/studio"})


def legal_page(template):
    from studio_mcp import MAX_FILE_BYTES, RETENTION_DAYS, USER_QUOTA_BYTES

    return render_template(
        template,
        site_url=ARTCRAFT_SITE_URL,
        project_url=PROJECT_URL,
        product_url=ARTCRAFT_PRODUCT_URL,
        last_updated=LEGAL_LAST_UPDATED,
        # Studio deletes files this many days after the session ends.
        retention_days=RETENTION_DAYS,
        max_file_mb=MAX_FILE_BYTES // 1_000_000,
        quota_mb=USER_QUOTA_BYTES // 1_000_000,
        support_email=os.environ.get("SUPPORT_EMAIL", "").strip(),
    )


@app.get("/privacy")
def privacy():
    return legal_page("privacy.html")


@app.get("/terms")
def terms():
    return legal_page("terms.html")


@app.get("/support")
def support():
    return legal_page("support.html")


@app.post("/account/disconnect")
@app.post("/account/close")
def end_account():
    """Disconnect and account close both delete the grant and the user's files now."""
    import connector_auth
    from media_storage import user_id_for
    from studio_mcp import _verifier, storage

    header = http_request.headers.get("Authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip() or _verifier is None:
        return jsonify({"error": "Sign in required."}), 401
    access = _verifier.verify_connector_token(value.strip())
    if access is None or not access.subject:
        return jsonify({"error": "Sign in required."}), 401
    user = user_id_for((access.claims or {}).get("iss", ""), access.subject)
    # Sync on purpose: this view runs on a worker thread, and the Craft session
    # lock belongs to the server loop.
    deleted = connector_auth.forget_user_files(storage(), user)
    return jsonify({"deleted_files": deleted})


@app.get("/status")
def status_page():
    import site_status

    return render_template(
        "status.html",
        site_url=ARTCRAFT_SITE_URL,
        project_url=PROJECT_URL,
        product_url=ARTCRAFT_PRODUCT_URL,
        org_url=ORG_URL,
        app_icons=APP_ICONS,
        status=site_status.summary(STARTED_AT),
    )


@app.get("/status.json")
def status_json():
    import site_status
    import usage
    from studio_mcp import storage

    try:
        counts = usage.cached_summary(storage())
    except Exception:  # noqa: BLE001 - the status page still answers without the bucket
        counts = None
    response = jsonify({**site_status.summary(STARTED_AT), "usage": counts})
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/")
def index():
    from site_status import NOT_HOSTED_APPS
    from studio_mcp import RETENTION_DAYS

    return render_template(
        "index.html",
        org_name=ORG_NAME,
        org_url=ORG_URL,
        project_url=PROJECT_URL,
        product_url=ARTCRAFT_PRODUCT_URL,
        site_url=ARTCRAFT_SITE_URL,
        app_icons=APP_ICONS,
        hosted_apps=HOSTED_APPS,
        not_hosted_apps=NOT_HOSTED_APPS,
        retention_days=RETENTION_DAYS,
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

    import craft_sessions
    import usage
    from studio_mcp import STUDIO_PATH, create_studio_app, storage, studio, sweep_expired_files_forever

    studio_app = create_studio_app(mcp_allowed_hosts())

    # Count each connector's tool calls, and for Studio which Craft app they used:
    # named when a session opens, then found from the session id.
    app_names = {hosted["name"] for hosted in HOSTED_APPS}

    def craft_app_of(arguments):
        if arguments.get("app") in app_names:
            return arguments["app"]
        session = craft_sessions.manager.sessions.get(str(arguments.get("session_id", "")))
        return session.app.name if session else None

    usage.timed_tools(mcp, "catalog")
    usage.timed_tools(studio, "studio", craft_app_of)

    @asynccontextmanager
    async def lifespan(_app):
        async with mcp.session_manager.run(), studio.session_manager.run():
            sweeper = asyncio.create_task(sweep_expired_files_forever())
            counter = asyncio.create_task(usage.flush_forever(storage))
            try:
                yield
            finally:
                sweeper.cancel()
                counter.cancel()
                await usage.flush_now(storage)
                await craft_sessions.manager.close_all()

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

    # MOVED_TO turns a retired deployment into a forwarder: every request except
    # the health check gets a permanent redirect to the same path on the new
    # site. 308 keeps the method and body, so MCP clients' POSTs follow it too.
    moved_to = os.environ.get("MOVED_TO", "").rstrip("/")

    async def dispatch(scope, receive, send):
        path = scope.get("path", "")
        if moved_to and scope["type"] == "http" and path != "/health":
            query = scope.get("query_string", b"").decode("latin-1")
            location = moved_to + path + (f"?{query}" if query else "")
            await send({"type": "http.response.start", "status": 308,
                        "headers": [(b"location", location.encode("latin-1")), (b"content-length", b"0")]})
            await send({"type": "http.response.body", "body": b""})
            return
        if scope["type"] in ("http", "websocket") and path.startswith(studio_prefixes):
            await studio_app(scope, receive, send)
        else:
            await site(scope, receive, send)

    return usage.counted_requests(dispatch)


asgi_app = create_asgi_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(asgi_app, host="0.0.0.0", port=int(os.environ.get("PORT", "5001")))
