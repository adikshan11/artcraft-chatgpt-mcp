# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Adithya Shankaran

"""BridgeCraft Studio: the public, multi-user MCP server for video, image, and PDF work.

Users sign in with OAuth (Auth0, Clerk, or any OIDC provider that issues JWT
access tokens). Every tool resolves the caller from their access token and only
touches that caller's files in storage. No personal API keys are used.
"""

import asyncio
import logging
import mimetypes
import os
import tempfile
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from mcp.server import MCPServer
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, Icon, ImageContent, TextContent, ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

import connector_auth
import craft_sessions
import log_redaction
import media_engine as engine
import usage

log_redaction.install()
from media_storage import (
    StorageError,
    media_kind,
    new_media_id,
    normalize_extension,
    storage_from_env,
    user_id_for,
    validate_media_id,
)

STUDIO_PATH = "/studio"
SITE_URL = os.environ.get("PUBLIC_BASE_URL", "https://bridgecraft.onrender.com").rstrip("/")
MAX_FILE_BYTES = int(os.environ.get("STUDIO_MAX_FILE_MB", "500")) * 1_000_000
USER_QUOTA_BYTES = int(os.environ.get("STUDIO_USER_QUOTA_MB", "5000")) * 1_000_000
log = logging.getLogger(__name__)
# Files under users/<id>/ are deleted this many days after the session ends.
# STUDIO_FILE_RETENTION_DAYS on the host is 7. Any other value is ignored so the
# privacy policy and the sweep stay on the same period.
def _retention_days() -> int:
    configured = os.environ.get("STUDIO_FILE_RETENTION_DAYS", "7").strip()
    if configured != "7":
        log.warning("STUDIO_FILE_RETENTION_DAYS is not 7; files are kept 7 days after the session ends.")
    return 7


RETENTION_DAYS = _retention_days()
SWEEP_INTERVAL_SECONDS = 3600
STUDIO_INSTRUCTIONS = (
    "BridgeCraft Studio edits the signed-in user's own media. Start by importing a file "
    "with import_chat_files (files attached in the chat), import_media_from_url, or "
    "create_upload_link, then pass the returned media_id "
    "to video, image, or PDF tools. Every edit creates a new file and returns a "
    "download_url; originals are never modified. Only sign PDFs when the user explicitly "
    "asks to sign as themselves. For documents, use the Craft apps: list_craft_apps, then "
    "open_craft_session with the app and media_ids (describe_craft_tools gives full schemas "
    "when the session lists only tool names), call run_craft_tool with the app's own "
    "tools (save with the app's save tool, e.g. save_document to a new filename), then "
    "save_craft_files to store the results and close_craft_session when done. "
    "ArtCraft generation and library reads use the signed-in user's ArtCraft grant, "
    "not the connector sign-in token. A paid generation starts only after the user "
    "agrees to the price, passed as max_credits. disconnect_account and close_account "
    "delete the user's files and that grant immediately."
)


class JwtTokenVerifier:
    """Verifies OAuth JWT access tokens against the provider's JWKS.

    The audience is always the connector resource. A token for any other audience
    is rejected, including when ``OAUTH_AUDIENCE`` names something else.
    """

    def __init__(self, issuer: str, jwks_url: str | None = None):
        import jwt

        self.issuer = issuer
        self.audience = connector_auth.CONNECTOR_AUDIENCE
        self._jwks = jwt.PyJWKClient(
            jwks_url or f"{issuer.rstrip('/')}/.well-known/jwks.json", cache_keys=True, lifespan=3600
        )

    def verify_connector_token(self, token: str) -> AccessToken | None:
        try:
            signing_key = self._jwks.get_signing_key_from_jwt(token)
        except Exception:
            return None
        return connector_auth.access_token_from_jwt(token, signing_key.key, self.issuer)

    async def verify_token(self, token: str) -> AccessToken | None:
        import anyio.to_thread

        return await anyio.to_thread.run_sync(self.verify_connector_token, token)


def _auth_config():
    issuer = os.environ.get("OAUTH_ISSUER")
    if not issuer:
        return None, None
    verifier = JwtTokenVerifier(issuer, os.environ.get("OAUTH_JWKS_URL"))
    settings = AuthSettings(
        issuer_url=issuer,
        resource_server_url=connector_auth.CONNECTOR_AUDIENCE,
        service_documentation_url=SITE_URL,
        required_scopes=[connector_auth.CONNECTOR_SCOPE],
        validate_token_resource=True,
    )
    return verifier, settings


_verifier, _auth_settings = _auth_config()

studio = MCPServer(
    "artcraft-studio",
    title="BridgeCraft Studio",
    description="Edit video, images, and PDFs (including e-signatures) in your own BridgeCraft workspace.",
    website_url=SITE_URL,
    icons=[
        Icon(src=f"{SITE_URL}/static/bridgecraft-512.png", mimeType="image/png", sizes=["512x512"]),
        Icon(src=f"{SITE_URL}/static/bridgecraft.svg", mimeType="image/svg+xml"),
    ],
    version="1.0.0",
    instructions=STUDIO_INSTRUCTIONS,
    token_verifier=_verifier,
    auth=_auth_settings,
)


# --------------------------------------------------------------------------- #
# Identity, storage, and helpers
# --------------------------------------------------------------------------- #


def current_user() -> str:
    token = get_access_token()
    if token is not None and token.subject:
        user = user_id_for((token.claims or {}).get("iss", ""), token.subject)
    else:
        dev_user = os.environ.get("STUDIO_DEV_USER")
        if not (dev_user and not os.environ.get("RENDER")):
            raise ToolError("Sign in to BridgeCraft Studio to use this tool.")
        user = user_id_for("local-dev", dev_user)
    connector_auth.note_activity(storage(), user)
    return user


@lru_cache(maxsize=1)
def storage():
    return storage_from_env()


def delete_expired_files() -> int:
    """Delete files under users/<id>/ whose session ended at least RETENTION_DAYS ago."""
    store = storage()
    now = datetime.now(timezone.utc)
    deleted = connector_auth.expire_stored_files(store, now, RETENTION_DAYS, craft_sessions.IDLE_SECONDS)
    connector_auth.sweep_expired_grants(store, now)
    return deleted


# The last sweep's time and outcome, for the /status page.
last_sweep: dict = {}


async def sweep_expired_files_forever():
    """Run delete_expired_files at startup and then every hour; a failed sweep is retried next time."""
    while True:
        try:
            deleted = await asyncio.to_thread(delete_expired_files)
            last_sweep.update(at=datetime.now(timezone.utc), ok=True, deleted=deleted)
            if deleted:
                log.info("Deleted %d files whose session ended at least %d days ago.", deleted, RETENTION_DAYS)
        except Exception:
            last_sweep.update(at=datetime.now(timezone.utc), ok=False, deleted=0)
            log.exception("Deleting expired files failed.")
        try:
            await asyncio.to_thread(usage.prune, storage())
        except Exception:
            log.exception("Deleting old usage counts failed.")
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)


class MediaItem(BaseModel):
    media_id: str
    kind: str
    size_bytes: int
    download_url: str = Field(description="Temporary link to view or download the file.")


class MediaList(BaseModel):
    items: list[MediaItem]
    used_bytes: int
    quota_bytes: int


class UploadLink(BaseModel):
    media_id: str
    upload_url: str
    method: str = "PUT"
    content_type: str
    expires_in_seconds: int


class MediaInfo(BaseModel):
    media_id: str
    kind: str
    details: dict


class PdfText(BaseModel):
    media_id: str
    text: str


class SignedPdf(MediaItem):
    signer_name: str
    signed_at: str
    original_sha256: str
    signed_sha256: str


class Workspace:
    """A temporary directory holding a user's inputs and one new output."""

    def __init__(self, user_id: str):
        self.user_id = user_id
        self._tmp = tempfile.TemporaryDirectory(prefix="studio-")
        self.root = Path(self._tmp.name)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._tmp.cleanup()

    def fetch(self, media_id: str, expected: set[str] | str | None = None) -> Path:
        validate_media_id(media_id)
        kinds = {expected} if isinstance(expected, str) else expected
        if kinds and media_kind(media_id) not in kinds:
            raise ToolError(f"'{media_id}' is a {media_kind(media_id)} file; expected {' or '.join(sorted(kinds))}.")
        path = self.root / f"in-{len(list(self.root.iterdir()))}-{media_id}"
        storage().get_file(self.user_id, media_id, path)
        return path

    def output(self, ext: str) -> Path:
        media_id = new_media_id(ext)
        return self.root / media_id

    def save(self, output: Path) -> MediaItem:
        size = output.stat().st_size
        _check_quota(self.user_id, size)
        storage().put_file(self.user_id, output.name, output)
        return _item(self.user_id, output.name, size)


def _item(user_id: str, media_id: str, size: int) -> MediaItem:
    return MediaItem(
        media_id=media_id,
        kind=media_kind(media_id),
        size_bytes=size,
        download_url=storage().download_url(user_id, media_id),
    )


def _check_quota(user_id: str, incoming: int):
    if incoming > MAX_FILE_BYTES:
        raise ToolError(f"Files are limited to {MAX_FILE_BYTES // 1_000_000} MB.")
    used = sum(obj.size_bytes for obj in storage().list(user_id))
    if used + incoming > USER_QUOTA_BYTES:
        raise ToolError("Your BridgeCraft Studio storage is full. Delete some files and try again.")


def _run(action):
    """Run a tool body, converting expected failures into user-facing tool errors."""
    try:
        return action()
    except (engine.MediaError, StorageError) as exc:
        raise ToolError(str(exc)) from exc


def _video_ext(fmt: str) -> str:
    if fmt not in ("mp4", "webm", "mov"):
        raise ToolError("Video format must be mp4, webm, or mov.")
    return fmt


READ = ToolAnnotations(read_only_hint=True, open_world_hint=False)
CREATE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)


# --------------------------------------------------------------------------- #
# Library
# --------------------------------------------------------------------------- #


@studio.tool(title="List my media", annotations=READ, structured_output=True)
def list_media(kind: Literal["all", "video", "image", "audio", "pdf", "document", "presentation", "project"] = "all", limit: int = 50) -> MediaList:
    """List files in the user's BridgeCraft Studio library, newest first."""
    user = current_user()

    def body():
        objects = storage().list(user)
        used = sum(obj.size_bytes for obj in objects)
        chosen = [obj for obj in objects if kind == "all" or obj.kind == kind][: max(1, min(limit, 200))]
        return MediaList(
            items=[_item(user, obj.media_id, obj.size_bytes) for obj in chosen],
            used_bytes=used,
            quota_bytes=USER_QUOTA_BYTES,
        )

    return _run(body)


@studio.tool(
    title="Import media from URL",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=True),
    structured_output=True,
)
def import_media_from_url(url: str, filename: str) -> MediaItem:
    """Copy a video, image, audio file, or PDF from a public https URL into the user's library.

    `filename` supplies the file type, for example "clip.mp4" or "contract.pdf".
    """
    user = current_user()

    def body():
        with Workspace(user) as ws:
            output = ws.output(normalize_extension(filename))
            engine.fetch_url(url, output, MAX_FILE_BYTES)
            return ws.save(output)

    return _run(body)


class ChatFile(BaseModel):
    """A file the chat app passes in. The four fields are the ones ChatGPT's file params require."""

    model_config = ConfigDict(extra="forbid")
    download_url: str
    file_id: str
    mime_type: str = ""
    file_name: str = ""


MAX_CHAT_FILES = 10


def _chat_file_extension(file: ChatFile) -> str:
    if file.file_name and "." in file.file_name:
        return normalize_extension(file.file_name)
    guessed = mimetypes.guess_extension(file.mime_type)
    if guessed:
        return normalize_extension(guessed)
    raise StorageError("The file has no name or type, so Studio can't tell what kind of file it is.")


@studio.tool(
    title="Import files from the chat",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=True),
    meta={"openai/fileParams": ["files"]},
    structured_output=True,
)
def import_chat_files(files: Annotated[list[ChatFile], Field(min_length=1, max_length=MAX_CHAT_FILES)]) -> MediaList:
    """Copy files the user attached in the chat into their library, then use the returned media_ids.

    The chat app fills in each file's download_url; don't invent one.
    """
    user = current_user()

    def body():
        _check_quota(user, 0)
        items = []
        for file in files:
            with Workspace(user) as ws:
                output = ws.output(_chat_file_extension(file))
                engine.fetch_url(file.download_url, output, MAX_FILE_BYTES)
                items.append(ws.save(output))
        used = sum(obj.size_bytes for obj in storage().list(user))
        return MediaList(items=items, used_bytes=used, quota_bytes=USER_QUOTA_BYTES)

    return _run(body)


@studio.tool(title="Create upload link", annotations=CREATE, structured_output=True)
def create_upload_link(filename: str) -> UploadLink:
    """Create a 15-minute link the user can PUT a file to directly, for large files.

    The upload must send the returned content_type as its Content-Type header.
    """
    user = current_user()

    def body():
        media_id = new_media_id(normalize_extension(filename))
        from media_storage import UPLOAD_URL_TTL_SECONDS, content_type_for

        _check_quota(user, 0)
        return UploadLink(
            media_id=media_id,
            upload_url=storage().upload_url(user, media_id),
            content_type=content_type_for(media_id),
            expires_in_seconds=UPLOAD_URL_TTL_SECONDS,
        )

    return _run(body)


@studio.tool(title="Get download link", annotations=READ, structured_output=True)
def get_download_link(media_id: str) -> MediaItem:
    """Get a fresh temporary download link for one of the user's files."""
    user = current_user()

    def body():
        validate_media_id(media_id)
        for obj in storage().list(user):
            if obj.media_id == media_id:
                return _item(user, media_id, obj.size_bytes)
        raise StorageError(f"Media '{media_id}' was not found.")

    return _run(body)


@studio.tool(
    title="Delete media",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=False),
)
def delete_media(media_id: str) -> str:
    """Permanently delete one file from the user's library. Confirm with the user first."""
    user = current_user()
    _run(lambda: storage().delete(user, validate_media_id(media_id)))
    return f"Deleted {media_id}."


@studio.tool(title="Inspect media", annotations=READ, structured_output=True)
def inspect_media(media_id: str) -> MediaInfo:
    """Show duration, resolution, codecs, page count, or image size for a file."""
    user = current_user()

    def body():
        with Workspace(user) as ws:
            path = ws.fetch(media_id)
            kind = media_kind(media_id)
            if kind == "pdf":
                details = engine.pdf_info(path)
            elif kind == "image":
                image = engine._open_image(path)
                details = {"width": image.width, "height": image.height, "mode": image.mode}
            elif kind in ("document", "presentation", "project"):
                details = {"size_bytes": path.stat().st_size,
                           "hint": "Open it with open_craft_session to read or edit it."}
            else:
                details = engine.probe(path)
            return MediaInfo(media_id=media_id, kind=kind, details=details)

    return _run(body)


# --------------------------------------------------------------------------- #
# Video and audio
# --------------------------------------------------------------------------- #


@studio.tool(title="Trim video or audio", annotations=CREATE, structured_output=True)
def trim_video(media_id: str, start_seconds: float, end_seconds: float | None = None,
               output_format: str = "mp4") -> MediaItem:
    """Cut a clip from `start_seconds` to `end_seconds` (or to the end)."""
    user = current_user()

    def body():
        with Workspace(user) as ws:
            source = ws.fetch(media_id, "video")
            output = ws.output(_video_ext(output_format))
            engine.video_trim(source, output, start_seconds, end_seconds)
            return ws.save(output)

    return _run(body)


@studio.tool(title="Transform video", annotations=CREATE, structured_output=True)
def transform_video(
    media_id: str,
    max_height: int | None = None,
    speed: float = 1.0,
    mute: bool = False,
    rotate_degrees: Literal[0, 90, 180, 270] = 0,
    crop_x: int | None = None,
    crop_y: int | None = None,
    crop_width: int | None = None,
    crop_height: int | None = None,
    output_format: str = "mp4",
) -> MediaItem:
    """Resize (e.g. max_height=720), change speed (0.25-4x), mute, rotate, crop, or convert format."""
    user = current_user()
    crop_values = (crop_x, crop_y, crop_width, crop_height)
    if any(value is not None for value in crop_values) and None in crop_values:
        raise ToolError("Cropping needs crop_x, crop_y, crop_width, and crop_height.")

    def body():
        with Workspace(user) as ws:
            source = ws.fetch(media_id, "video")
            output = ws.output(_video_ext(output_format))
            engine.video_transform(source, output, max_height, speed, mute,
                                   crop_values if crop_x is not None else None, rotate_degrees)
            return ws.save(output)

    return _run(body)


@studio.tool(title="Join videos", annotations=CREATE, structured_output=True)
def join_videos(media_ids: list[str], width: int = 1920, height: int = 1080,
                output_format: str = "mp4") -> MediaItem:
    """Join 2-20 clips in order into one video, letterboxing them to width x height."""
    user = current_user()
    if not 2 <= len(media_ids) <= 20:
        raise ToolError("Join between 2 and 20 clips.")
    if not (16 <= width <= 3840 and 16 <= height <= 2160):
        raise ToolError("Output size must be at most 3840x2160.")

    def body():
        with Workspace(user) as ws:
            sources = [ws.fetch(media_id, "video") for media_id in media_ids]
            output = ws.output(_video_ext(output_format))
            engine.video_concat(sources, output, width - width % 2, height - height % 2)
            return ws.save(output)

    return _run(body)


@studio.tool(title="Add audio to video", annotations=CREATE, structured_output=True)
def add_audio_to_video(video_id: str, audio_id: str, replace_original: bool = False,
                       music_volume: float = 0.35) -> MediaItem:
    """Add a music or voice-over track, mixed under the original audio or replacing it."""
    user = current_user()

    def body():
        with Workspace(user) as ws:
            video = ws.fetch(video_id, "video")
            audio = ws.fetch(audio_id, {"audio", "video"})
            output = ws.output("mp4")
            engine.video_add_audio(video, audio, output, replace_original, max(0.0, min(music_volume, 2.0)))
            return ws.save(output)

    return _run(body)


@studio.tool(title="Add caption to video", annotations=CREATE, structured_output=True)
def add_text_to_video(media_id: str, text: str, position: Literal["top", "center", "bottom"] = "bottom",
                      font_size: int = 48, start_seconds: float | None = None,
                      end_seconds: float | None = None) -> MediaItem:
    """Burn a title or caption into the video, optionally only between two timestamps."""
    user = current_user()

    def body():
        with Workspace(user) as ws:
            source = ws.fetch(media_id, "video")
            output = ws.output("mp4")
            engine.video_add_text(source, output, text, position, max(8, min(font_size, 200)),
                                  start_seconds, end_seconds)
            return ws.save(output)

    return _run(body)


@studio.tool(title="Extract audio", annotations=CREATE, structured_output=True)
def extract_audio(media_id: str, output_format: Literal["mp3", "wav", "m4a"] = "mp3") -> MediaItem:
    """Save a video's soundtrack as an audio file."""
    user = current_user()

    def body():
        with Workspace(user) as ws:
            source = ws.fetch(media_id, "video")
            output = ws.output(output_format)
            engine.extract_audio(source, output)
            return ws.save(output)

    return _run(body)


@studio.tool(title="Capture video frame", annotations=CREATE, structured_output=True)
def capture_frame(media_id: str, at_seconds: float = 0.0,
                  output_format: Literal["png", "jpg"] = "png") -> MediaItem:
    """Save one frame of a video as an image, for thumbnails or further image editing."""
    user = current_user()

    def body():
        with Workspace(user) as ws:
            source = ws.fetch(media_id, "video")
            output = ws.output(output_format)
            engine.video_frame(source, output, at_seconds)
            return ws.save(output)

    return _run(body)


# --------------------------------------------------------------------------- #
# Images
# --------------------------------------------------------------------------- #


@studio.tool(title="Edit image", annotations=CREATE, structured_output=True)
def edit_image(media_id: str, operations: list[dict], output_format: str | None = None,
               quality: int = 90) -> MediaItem:
    """Apply a sequence of image edits and save the result as a new image.

    Each operation is an object with "op" plus parameters, applied in order:
    crop {left, top, right, bottom}; resize {width?, height?}; rotate {degrees};
    flip_horizontal; flip_vertical; brightness/contrast/saturation/sharpness {factor 0-5, 1 = unchanged};
    blur {radius}; sharpen {percent}; grayscale; sepia; invert; auto_contrast;
    border {size, color}; text {text, x, y, size, color, stroke_color}.
    """
    user = current_user()

    def body():
        with Workspace(user) as ws:
            source = ws.fetch(media_id, "image")
            ext = normalize_extension(output_format or media_id)
            if media_kind(f"x.{ext}") != "image":
                raise ToolError("Output format must be an image type.")
            output = ws.output(ext)
            engine.image_edit(source, output, operations, quality)
            return ws.save(output)

    return _run(body)


@studio.tool(title="Layer images", annotations=CREATE, structured_output=True)
def layer_images(base_id: str, overlay_id: str, x: int = 0, y: int = 0, scale: float = 1.0,
                 opacity: float = 1.0) -> MediaItem:
    """Place one image (logo, sticker, cut-out) on top of another at x, y; saves a PNG."""
    user = current_user()

    def body():
        with Workspace(user) as ws:
            base = ws.fetch(base_id, "image")
            overlay = ws.fetch(overlay_id, "image")
            output = ws.output("png")
            engine.image_composite(base, overlay, output, x, y, max(0.01, min(scale, 10.0)),
                                   max(0.0, min(opacity, 1.0)))
            return ws.save(output)

    return _run(body)


# --------------------------------------------------------------------------- #
# PDFs
# --------------------------------------------------------------------------- #


@studio.tool(title="Read PDF text", annotations=READ, structured_output=True)
def read_pdf_text(media_id: str, pages: list[int] | None = None) -> PdfText:
    """Extract text from all pages or the listed 1-based pages."""
    user = current_user()

    def body():
        with Workspace(user) as ws:
            return PdfText(media_id=media_id, text=engine.pdf_extract_text(ws.fetch(media_id, "pdf"), pages))

    return _run(body)


@studio.tool(title="Organize PDF pages", annotations=CREATE, structured_output=True)
def organize_pdf(media_id: str, pages: list[int], rotate: dict[str, int] | None = None) -> MediaItem:
    """Create a new PDF from the listed 1-based pages in that order.

    Omit pages to delete them, reorder to rearrange, list a subset to extract. `rotate`
    maps a page number to degrees, e.g. {"2": 90}.
    """
    user = current_user()

    def body():
        with Workspace(user) as ws:
            source = ws.fetch(media_id, "pdf")
            output = ws.output("pdf")
            engine.pdf_organize(source, output, pages, rotate)
            return ws.save(output)

    return _run(body)


@studio.tool(title="Merge PDFs", annotations=CREATE, structured_output=True)
def merge_pdfs(media_ids: list[str]) -> MediaItem:
    """Combine 2-50 PDFs in order into one new PDF."""
    user = current_user()
    if not 2 <= len(media_ids) <= 50:
        raise ToolError("Merge between 2 and 50 PDFs.")

    def body():
        with Workspace(user) as ws:
            sources = [ws.fetch(media_id, "pdf") for media_id in media_ids]
            output = ws.output("pdf")
            engine.pdf_merge(sources, output)
            return ws.save(output)

    return _run(body)


@studio.tool(title="Add text to PDF", annotations=CREATE, structured_output=True)
def add_text_to_pdf(media_id: str, page: int, text: str, x: float, y: float,
                    font_size: float = 12, color: str = "#000000") -> MediaItem:
    """Write text onto a page. x and y are fractions (0-1) of the page width/height from the top-left."""
    user = current_user()
    if not (0 <= x <= 1 and 0 <= y <= 1):
        raise ToolError("x and y must be between 0 and 1.")

    def body():
        with Workspace(user) as ws:
            source = ws.fetch(media_id, "pdf")
            output = ws.output("pdf")
            engine.pdf_add_text(source, output, page, text, x, y, max(4, min(font_size, 96)), color)
            return ws.save(output)

    return _run(body)


@studio.tool(title="Fill PDF form", annotations=CREATE, structured_output=True)
def fill_pdf_form(media_id: str, values: dict[str, str]) -> MediaItem:
    """Fill a fillable PDF form. Use inspect_media first to confirm it has form fields."""
    user = current_user()

    def body():
        with Workspace(user) as ws:
            source = ws.fetch(media_id, "pdf")
            output = ws.output("pdf")
            engine.pdf_fill_form(source, output, values)
            return ws.save(output)

    return _run(body)


@studio.tool(title="Watermark PDF", annotations=CREATE, structured_output=True)
def watermark_pdf(media_id: str, text: str, opacity: float = 0.15) -> MediaItem:
    """Stamp diagonal text such as DRAFT or CONFIDENTIAL across every page."""
    user = current_user()

    def body():
        with Workspace(user) as ws:
            source = ws.fetch(media_id, "pdf")
            output = ws.output("pdf")
            engine.pdf_watermark(source, output, text, opacity)
            return ws.save(output)

    return _run(body)


@studio.tool(title="Sign PDF", annotations=CREATE, structured_output=True)
def sign_pdf(
    media_id: str,
    signer_name: str,
    page: int | None = None,
    position: Literal["bottom-right", "bottom-left", "bottom-center", "top-right"] = "bottom-right",
    signature_image_id: str | None = None,
    reason: str | None = None,
) -> SignedPdf:
    """Electronically sign a PDF as the signed-in user.

    Adds a visible signature (typed name or the user's signature image) and an audit-trail
    page with the original document's SHA-256 hash. Only call this when the user explicitly
    asks to sign as themselves. Page defaults to the last page.
    """
    user = current_user()

    def body():
        with Workspace(user) as ws:
            source = ws.fetch(media_id, "pdf")
            signature = ws.fetch(signature_image_id, "image") if signature_image_id else None
            output = ws.output("pdf")
            record = engine.pdf_sign(source, output, signer_name, user, page, position, signature, reason)
            item = ws.save(output)
            return SignedPdf(**item.model_dump(), **record)

    return _run(body)


# --------------------------------------------------------------------------- #
# Hosted Craft apps
# --------------------------------------------------------------------------- #

MAX_CRAFT_TEXT_CHARS = 100_000
MAX_CRAFT_IMAGE_CHARS = 8_000_000  # base64 characters per returned image
# Up to this many tools, open_craft_session includes full schemas; above it (PdfCraft has
# ~170), it lists names and short descriptions and describe_craft_tools gives the rest.
MAX_INLINE_TOOL_SCHEMAS = 40
SHORT_DESCRIPTION_CHARS = 120


class CraftAppInfo(BaseModel):
    name: str
    title: str
    version: str
    description: str
    file_types: list[str]
    website: str


class CraftAppList(BaseModel):
    apps: list[CraftAppInfo]


class CraftToolInfo(BaseModel):
    name: str
    description: str
    input_schema: dict | None = None


class CraftSessionInfo(BaseModel):
    session_id: str
    app: str
    files: list[str] = Field(description="Files in the session folder, by the path to pass to the app's tools.")
    tools: list[CraftToolInfo]
    schemas_included: bool = Field(
        description="False when the app has many tools: call describe_craft_tools for the ones you need.")
    idle_minutes: int


class CraftToolDetails(BaseModel):
    tools: list[CraftToolInfo]
    unknown: list[str]


class CraftFiles(BaseModel):
    session_id: str
    files: list[str]


class SkippedFile(BaseModel):
    path: str
    reason: str


class SavedCraftFile(MediaItem):
    path: str


class CraftSaveResult(BaseModel):
    saved: list[SavedCraftFile]
    skipped: list[SkippedFile]


def _craft(action):
    try:
        return action()
    except (craft_sessions.CraftError, StorageError) as exc:
        raise ToolError(str(exc)) from exc


async def _craft_async(coro):
    try:
        return await coro
    except (craft_sessions.CraftError, StorageError) as exc:
        raise ToolError(str(exc)) from exc


def _copy_into(user_id: str, app: craft_sessions.CraftApp, media_ids: list[str], root: Path) -> list[str]:
    """Copy the user's files into a session folder, named by their media ids."""
    names = []
    for media_id in media_ids:
        validate_media_id(media_id)
        ext = media_id.rsplit(".", 1)[1]
        if ext not in app.extensions:
            raise StorageError(f"{app.title} cannot open .{ext} files. It handles: {', '.join(app.extensions)}.")
        if not (root / media_id).exists():
            storage().get_file(user_id, media_id, root / media_id)
        names.append(media_id)
    return names


def _craft_result(raw: dict) -> CallToolResult:
    content = []
    for block in raw.get("content") or []:
        if block.get("type") == "text":
            text = block.get("text", "")
            if len(text) > MAX_CRAFT_TEXT_CHARS:
                text = text[:MAX_CRAFT_TEXT_CHARS] + "\n[... output truncated by Studio]"
            content.append(TextContent(type="text", text=text))
        elif block.get("type") == "image" and len(block.get("data", "")) <= MAX_CRAFT_IMAGE_CHARS:
            content.append(ImageContent(type="image", data=block["data"], mimeType=block.get("mimeType", "image/png")))
        else:
            content.append(TextContent(type="text", text=f"[{block.get('type', 'unknown')} content omitted by Studio]"))
    return CallToolResult(content=content or [TextContent(type="text", text="(no output)")],
                          isError=bool(raw.get("isError")))


def _tool_info(tool: dict, full: bool) -> CraftToolInfo:
    description = " ".join((tool.get("description") or "").split())
    if not full and len(description) > SHORT_DESCRIPTION_CHARS:
        description = description[: SHORT_DESCRIPTION_CHARS - 1].rstrip() + "…"
    return CraftToolInfo(name=tool["name"], description=description,
                         input_schema=(tool.get("inputSchema") or {}) if full else None)


@studio.tool(title="List Craft apps", annotations=READ, structured_output=True)
async def list_craft_apps() -> CraftAppList:
    """List the open-source Craft apps (e.g. WordCraft) that Studio can run on your files."""
    current_user()
    await craft_sessions.manager.reap_idle()
    return CraftAppList(apps=[
        CraftAppInfo(name=a.name, title=a.title, version=a.version, description=a.description,
                     file_types=list(a.extensions), website=a.website)
        for a in craft_sessions.installed_apps()
    ])


@studio.tool(title="Open Craft session", annotations=CREATE, structured_output=True)
async def open_craft_session(app: str, media_ids: list[str] | None = None) -> CraftSessionInfo:
    """Start a Craft app for the signed-in user, with copies of the given files.

    Returns the session id, the files in its private folder, and the app's own tools.
    Pass file paths to those tools exactly as listed. Originals are never changed.
    """
    user = current_user()
    craft_app = _craft(lambda: craft_sessions.get_app(app))
    ids = list(media_ids or [])
    session = await _craft_async(craft_sessions.manager.open(
        user, craft_app, lambda root: _copy_into(user, craft_app, ids, root)))
    full = len(session.tools) <= MAX_INLINE_TOOL_SCHEMAS
    return CraftSessionInfo(
        session_id=session.id,
        app=craft_app.name,
        files=sorted(session.files()),
        tools=[_tool_info(t, full) for t in session.tools],
        schemas_included=full,
        idle_minutes=craft_sessions.IDLE_SECONDS // 60,
    )


@studio.tool(title="Describe Craft tools", annotations=READ, structured_output=True)
async def describe_craft_tools(session_id: str, tools: list[str]) -> CraftToolDetails:
    """Full descriptions and input schemas for some of a session's tools (up to 20 at a time)."""
    user = current_user()
    session = _craft(lambda: craft_sessions.manager.get(user, session_id))
    by_name = {t["name"]: t for t in session.tools}
    wanted = list(dict.fromkeys(tools))[:20]
    return CraftToolDetails(
        tools=[_tool_info(by_name[name], True) for name in wanted if name in by_name],
        unknown=[name for name in wanted if name not in by_name],
    )


@studio.tool(title="Add files to Craft session", annotations=CREATE, structured_output=True)
async def add_craft_files(session_id: str, media_ids: list[str]) -> CraftFiles:
    """Copy more of the user's files into an open Craft session (for example a picture to insert)."""
    user = current_user()
    session = _craft(lambda: craft_sessions.manager.get(user, session_id))
    await _craft_async(asyncio.to_thread(_copy_into, user, session.app, media_ids, session.root))
    for media_id in media_ids:  # newly added inputs are not results to save
        path = session.root / media_id
        session.snapshot[media_id] = (path.stat().st_size, path.stat().st_mtime_ns)
    return CraftFiles(session_id=session.id, files=sorted(session.files()))


@studio.tool(
    title="Run Craft tool",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False),
)
async def run_craft_tool(session_id: str, tool: str, arguments: dict | None = None) -> CallToolResult:
    """Call one of the session's Craft app tools (listed by open_craft_session) with its arguments.

    Files are only stored in the user's library after save_craft_files.
    """
    user = current_user()
    session = _craft(lambda: craft_sessions.manager.get(user, session_id))
    raw = await _craft_async(craft_sessions.manager.call(session, tool, arguments or {}))
    return _craft_result(raw)


@studio.tool(title="Save Craft files", annotations=CREATE, structured_output=True)
async def save_craft_files(session_id: str) -> CraftSaveResult:
    """Store every file the app created or changed since the last save as new library files.

    Call the app's own save tool first; open documents are only written to the folder then.
    """
    user = current_user()
    session = _craft(lambda: craft_sessions.manager.get(user, session_id))

    def body():
        saved, skipped = [], []
        for rel, path in sorted(session.changed_files().items()):
            try:
                media_id = new_media_id(rel)
                size = path.stat().st_size
                _check_quota(user, size)
                storage().put_file(user, media_id, path)
            except (StorageError, ToolError) as exc:
                skipped.append(SkippedFile(path=rel, reason=str(exc)))
                continue
            saved.append(SavedCraftFile(**_item(user, media_id, size).model_dump(), path=rel))
            session.snapshot[rel] = (size, path.stat().st_mtime_ns)
        return CraftSaveResult(saved=saved, skipped=skipped)

    return await _craft_async(asyncio.to_thread(body))


@studio.tool(
    title="Close Craft session",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=False),
)
async def close_craft_session(session_id: str) -> str:
    """Stop the app and delete the session folder. Unsaved changes are lost; save first."""
    user = current_user()
    await _craft_async(craft_sessions.manager.close(user, session_id))
    return f"Closed {session_id}."


def create_studio_app(allowed_hosts: list[str]):
    return studio.streamable_http_app(
        streamable_http_path=STUDIO_PATH,
        transport_security=TransportSecuritySettings(allowed_hosts=allowed_hosts),
    )


import studio_artcraft

studio_artcraft.register_artcraft_tools(studio)
