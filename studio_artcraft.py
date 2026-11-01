# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Adithya Shankaran

"""ArtCraft generation and library reads on the hosted connector.

These tools run on the signed-in request. They call ArtCraft with the exchanged
grant only. The connector token is never attached to the request, and the grant
is never placed in the tool result.
"""

import asyncio
from typing import Literal

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel

import artcraft_api
import connector_auth
import studio_mcp

READ = ToolAnnotations(read_only_hint=True, open_world_hint=True)
CREATE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True)


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


class LibraryItem(BaseModel):
    media_token: str
    title: str
    project_type: str
    media_class: str
    created_at: str


class LibraryList(BaseModel):
    items: list[LibraryItem]


def _artcraft(purpose: str):
    """Return ``(user id, exchanged bearer)`` for this request."""
    access = get_access_token()
    user = studio_mcp.current_user()
    if access is None or not access.token:
        raise ToolError("Sign in with ArtCraft before using generation or the library.")
    issuer = (access.claims or {}).get("iss") or ""
    try:
        bearer = connector_auth.artcraft_bearer(
            studio_mcp.storage(), user, access.token, issuer, purpose
        )
    except connector_auth.GrantError as exc:
        raise ToolError(str(exc)) from exc
    return user, bearer


async def _call(purpose: str, action):
    user, bearer = _artcraft(purpose)
    try:
        return await asyncio.to_thread(action, bearer)
    except artcraft_api.ArtCraftRevoked as exc:
        # The grant is gone. Stored files stay until 7 days after this session ends.
        await connector_auth.revoke(studio_mcp.storage(), user)
        raise ToolError(str(exc)) from exc
    except artcraft_api.ArtCraftAPIError as exc:
        raise ToolError(str(exc)) from exc


def register_artcraft_tools(studio):
    @studio.tool(title="List ArtCraft models", annotations=READ, structured_output=True)
    def list_artcraft_models(kind: Literal["image", "video"] = "image") -> ModelList:
        """List ArtCraft's image or video models and the options each accepts.

        The catalogue is public. This call does not send the connector token.
        """
        studio_mcp.current_user()
        try:
            return ModelList(**artcraft_api.list_models(kind))
        except artcraft_api.ArtCraftAPIError as exc:
            raise ToolError(str(exc)) from exc

    @studio.tool(title="Estimate an ArtCraft generation's cost", annotations=READ, structured_output=True)
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
        """Ask ArtCraft what a generation would cost, without spending anything or sending a token."""
        studio_mcp.current_user()
        try:
            return CostEstimate(**artcraft_api.estimate_cost(
                kind, model, aspect_ratio=aspect_ratio, resolution=resolution,
                duration_seconds=duration_seconds, batch_count=batch_count,
                generate_audio=generate_audio, with_start_frame=with_start_frame,
            ))
        except artcraft_api.ArtCraftAPIError as exc:
            raise ToolError(str(exc)) from exc

    @studio.tool(title="Generate an ArtCraft image", annotations=CREATE, structured_output=True)
    async def generate_artcraft_image(
        prompt: str, model: str, image_batch_count: int = 1,
        aspect_ratio: str | None = None, max_credits: int | None = None,
    ) -> GenerationSubmission:
        """Submit an image-generation job. A paid model starts only when max_credits covers its cost."""
        def action(bearer):
            return artcraft_api.submit_image_generation(
                prompt, model, image_batch_count, aspect_ratio, max_credits, bearer=bearer
            )
        return GenerationSubmission(**await _call("generation", action))

    @studio.tool(title="Generate an ArtCraft video", annotations=CREATE, structured_output=True)
    async def generate_artcraft_video(
        prompt: str, model: str, aspect_ratio: str | None = None, resolution: str | None = None,
        duration_seconds: int | None = None, generate_audio: bool | None = None,
        start_frame_image_url: str | None = None, max_credits: int | None = None,
    ) -> GenerationSubmission:
        """Submit a video-generation job. A paid model starts only when max_credits covers its cost."""
        def action(bearer):
            return artcraft_api.submit_video_generation(
                prompt, model, aspect_ratio, resolution, duration_seconds, generate_audio,
                start_frame_image_url, max_credits, bearer=bearer,
            )
        return GenerationSubmission(**await _call("generation", action))

    @studio.tool(title="Check an ArtCraft job", annotations=READ, structured_output=True)
    async def get_artcraft_job(job_token: str) -> JobStatus:
        """Check an ArtCraft image or video job and return its media URL when complete."""
        return JobStatus(**await _call("status", lambda bearer: artcraft_api.get_job(job_token, bearer=bearer)))

    @studio.tool(title="List my ArtCraft library", annotations=READ, structured_output=True)
    async def list_artcraft_library() -> LibraryList:
        """List saved projects in the signed-in user's ArtCraft library."""
        return LibraryList(**await _call("library", artcraft_api.list_library))

    @studio.tool(
        title="Disconnect",
        annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=False),
    )
    async def disconnect_account() -> str:
        """Delete the signed-in user's files and ArtCraft grant now, and close their sessions."""
        user = studio_mcp.current_user()
        await connector_auth.forget_user(studio_mcp.storage(), user)
        return "Disconnected. Your files and ArtCraft grant were deleted."

    @studio.tool(
        title="Close account",
        annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=False),
    )
    async def close_account() -> str:
        """Delete the signed-in user's files and ArtCraft grant now. This closes the account on Studio."""
        user = studio_mcp.current_user()
        await connector_auth.forget_user(studio_mcp.storage(), user)
        return "Account closed. Your files and ArtCraft grant were deleted."
