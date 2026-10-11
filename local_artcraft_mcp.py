from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import Icon, ToolAnnotations
from pydantic import BaseModel

from app import (
    ARTCRAFT_APP_DESCRIPTION,
    ARTCRAFT_ICON_URL,
    ARTCRAFT_SITE_URL,
    GitHubRequestError,
    ProjectDetails,
    ProjectList,
    get_project_details,
    list_projects,
    search_projects,
)
from artcraft_api import ArtCraftAPIError, get_image_job, submit_image_generation
from craft_app_mcp import craft_app_lifespan, register_photocraft_tools
from pdfcraft_mcp import register_pdfcraft_tools


server = MCPServer(
    "artcraft",
    title="ArtCraft",
    description=ARTCRAFT_APP_DESCRIPTION,
    website_url=ARTCRAFT_SITE_URL,
    version="0.1.0",
    icons=[Icon(src=ARTCRAFT_ICON_URL, mimeType="image/svg+xml")],
    instructions=(
        "This unified ArtCraft connection provides public Storytold project discovery and "
        "ArtCraft image generation. Repository tools are read-only. Create images only when "
        "explicitly requested; generation can consume ArtCraft credits. After submitting a "
        "job, use get_artcraft_image_job to check its status. The image server reads its API "
        "key from macOS Keychain; never ask the user to paste their key into ChatGPT. "
        "PdfCraft can inspect, search, extract text from, combine, extract pages from, and "
        "rotate PDFs inside ~/Documents. PdfCraft writes new outputs only and refuses to "
        "overwrite existing files. PhotoCraft's native MCP tools are available through a "
        "workspace-confined bridge; PhotoCraft can access only ~/Documents/ArtCraft. "
        "Discover its native tool names with list_photocraft_tools before invoking them. "
        "Other Craft apps are discoverable here, but their editing operations are not exposed."
    ),
    lifespan=craft_app_lifespan,
)


class ImageGenerationSubmission(BaseModel):
    job_token: str
    model: str
    status: str
    message: str


class ImageJobStatus(BaseModel):
    job_token: str
    status: str
    progress_percentage: int | None
    image_url: str | None
    failure_category: str | None
    message: str | None


@server.tool(
    title="List ArtCraft apps and projects",
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    structured_output=True,
)
def list_artcraft_projects(limit: int = 30) -> ProjectList:
    """List public Storytold repositories for ArtCraft and its creative apps."""
    try:
        return ProjectList(projects=list_projects(limit))
    except GitHubRequestError as exc:
        raise ToolError(str(exc)) from exc


@server.tool(
    title="Search ArtCraft apps and projects",
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    structured_output=True,
)
def search_artcraft_projects(query: str, limit: int = 10) -> ProjectList:
    """Search public Storytold repositories by app or project name and description."""
    try:
        return ProjectList(projects=search_projects(query, limit))
    except (GitHubRequestError, ValueError) as exc:
        raise ToolError(str(exc)) from exc


@server.tool(
    title="Read an ArtCraft app or project",
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    structured_output=True,
)
def get_artcraft_project(name: str) -> ProjectDetails:
    """Read public details and the README for a Storytold repository."""
    try:
        return get_project_details(name)
    except (GitHubRequestError, ValueError) as exc:
        raise ToolError(str(exc)) from exc


@server.tool(
    title="Generate an ArtCraft image",
    annotations=ToolAnnotations(
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=False,
        open_world_hint=True,
    ),
    structured_output=True,
)
def generate_artcraft_image(
    prompt: str,
    model: str,
    image_batch_count: int = 1,
) -> ImageGenerationSubmission:
    """Submit an image-generation job to ArtCraft; generation may use account credits.

    Use an ArtCraft image model ID, for example flux_1_schnell. Start with one image
    unless the user explicitly requests a larger batch (maximum four). This returns a
    job token; call get_artcraft_image_job to retrieve the result when it is ready.
    """
    try:
        return ImageGenerationSubmission(
            **submit_image_generation(prompt, model, image_batch_count)
        )
    except ArtCraftAPIError as exc:
        raise ToolError(str(exc)) from exc


@server.tool(
    title="Check an ArtCraft image job",
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True),
    structured_output=True,
)
def get_artcraft_image_job(job_token: str) -> ImageJobStatus:
    """Check an ArtCraft image-generation job and return its image URL when complete."""
    try:
        return ImageJobStatus(**get_image_job(job_token))
    except ArtCraftAPIError as exc:
        raise ToolError(str(exc)) from exc


register_pdfcraft_tools(server)
register_photocraft_tools(server)


if __name__ == "__main__":
    server.run(transport="stdio")
