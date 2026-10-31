# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Adithya Shankaran

from typing import Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import Icon, ToolAnnotations
from pydantic import BaseModel

from app import (
    ARTCRAFT_APP_DESCRIPTION,
    BRAND_ICON_URL,
    ARTCRAFT_SITE_URL,
    GitHubRequestError,
    ProjectDetails,
    ProjectList,
    get_project_details,
    list_projects,
    search_projects,
)
from artcraft_api import (
    ArtCraftAPIError,
    estimate_cost,
    get_image_job,
    get_job,
    list_models,
    submit_image_generation,
    submit_video_generation,
)
from craft_app_mcp import craft_app_lifespan, register_photocraft_tools
from pdfcraft_mcp import register_pdfcraft_tools


server = MCPServer(
    "artcraft",
    title="BridgeCraft",
    description=ARTCRAFT_APP_DESCRIPTION,
    website_url=ARTCRAFT_SITE_URL,
    version="0.1.0",
    icons=[Icon(src=BRAND_ICON_URL, mimeType="image/svg+xml")],
    instructions=(
        "This unified ArtCraft connection provides public Storytold project discovery and "
        "ArtCraft image and video generation. Repository tools are read-only. Use "
        "list_artcraft_models to pick a model and its options, and estimate_artcraft_cost to "
        "tell the user the price. Generate only when explicitly requested: paid generations "
        "start only after the user agrees to the cost, passed as max_credits. After "
        "submitting, use get_artcraft_job to check the job. The server reads the API key "
        "from macOS Keychain; never ask the user to paste their key into the chat. "
        "PdfCraft can inspect, search, extract text from, combine, extract pages from, and "
        "rotate PDFs inside ~/Documents. PdfCraft writes new outputs only and refuses to "
        "overwrite existing files. PhotoCraft's native MCP tools are available through a "
        "workspace-confined bridge; PhotoCraft can access only ~/Documents/ArtCraft. "
        "Discover its native tool names with list_photocraft_tools before invoking them. "
        "Other Craft apps are discoverable here, but their editing operations are not exposed."
    ),
    lifespan=craft_app_lifespan,
)


class GenerationSubmission(BaseModel):
    job_token: str
    model: str
    status: str
    credits: int | None
    message: str


class ModelInfo(BaseModel):
    model: str
    name: str
    creator: str | None
    available_with_api_key: bool
    aspect_ratios: list[str]
    default_aspect_ratio: str | None
    resolutions: list[str]
    default_resolution: str | None
    min_seconds: int | None
    max_seconds: int | None
    max_batch: int | None
    start_frame_supported: bool | None
    sound_supported: bool | None


class ModelList(BaseModel):
    kind: str
    models: list[ModelInfo]


class CostEstimate(BaseModel):
    model: str
    is_free: bool
    credits: int | None
    usd_cents: int | None
    is_rate_limited: bool
    has_watermark: bool


class JobStatus(BaseModel):
    job_token: str
    status: str
    progress_percentage: int | None
    media_url: str | None
    failure_category: str | None
    message: str | None


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
    aspect_ratio: str | None = None,
    max_credits: int | None = None,
) -> GenerationSubmission:
    """Submit an image-generation job to ArtCraft.

    Use a model from list_artcraft_models (kind "image") that is available_with_api_key,
    for example flux_1_schnell. Start with one image unless the user asks for more
    (maximum four). Free models start straight away. A paid model starts only when
    max_credits covers its cost: tell the user the price first and pass the credits
    they agreed to. Returns a job token; call get_artcraft_job for the result.
    """
    try:
        return GenerationSubmission(
            **submit_image_generation(prompt, model, image_batch_count, aspect_ratio, max_credits)
        )
    except ArtCraftAPIError as exc:
        raise ToolError(str(exc)) from exc


@server.tool(
    title="List ArtCraft models",
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True),
    structured_output=True,
)
def list_artcraft_models(kind: Literal["image", "video"] = "image") -> ModelList:
    """List ArtCraft's image or video models and the options each accepts.

    Only models with available_with_api_key can be generated through this server.
    """
    try:
        return ModelList(**list_models(kind))
    except ArtCraftAPIError as exc:
        raise ToolError(str(exc)) from exc


@server.tool(
    title="Estimate an ArtCraft generation's cost",
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True),
    structured_output=True,
)
def estimate_artcraft_cost(
    kind: Literal["image", "video"],
    model: str,
    aspect_ratio: str | None = None,
    resolution: str | None = None,
    duration_seconds: int | None = None,
    batch_count: int = 1,
    generate_audio: bool | None = None,
    with_start_frame: bool = False,
) -> CostEstimate:
    """Ask ArtCraft what a generation would cost, without spending anything."""
    try:
        return CostEstimate(**estimate_cost(
            kind,
            model,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
            duration_seconds=duration_seconds,
            batch_count=batch_count,
            generate_audio=generate_audio,
            with_start_frame=with_start_frame,
        ))
    except ArtCraftAPIError as exc:
        raise ToolError(str(exc)) from exc


@server.tool(
    title="Generate an ArtCraft video",
    annotations=ToolAnnotations(
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=False,
        open_world_hint=True,
    ),
    structured_output=True,
)
def generate_artcraft_video(
    prompt: str,
    model: str,
    aspect_ratio: str | None = None,
    resolution: str | None = None,
    duration_seconds: int | None = None,
    generate_audio: bool | None = None,
    start_frame_image_url: str | None = None,
    max_credits: int | None = None,
) -> GenerationSubmission:
    """Submit a video-generation job to ArtCraft.

    Use a model from list_artcraft_models (kind "video") that is available_with_api_key,
    with options from its lists. start_frame_image_url (https) animates an image, if the
    model supports a start frame. A paid model starts only when max_credits covers its
    cost: tell the user the price first and pass the credits they agreed to. Returns a
    job token; call get_artcraft_job for the result.
    """
    try:
        return GenerationSubmission(**submit_video_generation(
            prompt,
            model,
            aspect_ratio,
            resolution,
            duration_seconds,
            generate_audio,
            start_frame_image_url,
            max_credits,
        ))
    except ArtCraftAPIError as exc:
        raise ToolError(str(exc)) from exc


@server.tool(
    title="Check an ArtCraft job",
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True),
    structured_output=True,
)
def get_artcraft_job(job_token: str) -> JobStatus:
    """Check an ArtCraft image or video job and return its media URL when complete."""
    try:
        return JobStatus(**get_job(job_token))
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
