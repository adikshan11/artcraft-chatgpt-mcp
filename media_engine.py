"""Media processing for ArtCraft Studio: video (FFmpeg), images (Pillow), PDFs.

Each function reads local input files and writes one new local output file.
Storage, auth, and tool wiring live in ``studio_mcp.py``.
"""

import hashlib
import io
import ipaddress
import json
import os
import socket
import subprocess
import warnings
from datetime import datetime, timezone
from pathlib import Path
from urllib import error, parse, request

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

FFMPEG = os.environ.get("FFMPEG_PATH", "ffmpeg")
FFPROBE = os.environ.get("FFPROBE_PATH", "ffprobe")
FFMPEG_TIMEOUT_SECONDS = int(os.environ.get("FFMPEG_TIMEOUT_SECONDS", "600"))
MAX_IMAGE_PIXELS = 80_000_000
MAX_VIDEO_HEIGHT = 2160
# Only container demuxers for ordinary media files. This blocks playlist and
# concat demuxers that could make FFmpeg read other files or network URLs.
FFMPEG_INPUT_GUARD = [
    "-protocol_whitelist", "file",
    "-format_whitelist", "mov,mp4,m4a,3gp,3g2,mj2,matroska,webm,mp3,wav,ogg,flac,aac,image2,png_pipe,jpeg_pipe",
]

Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
warnings.simplefilter("error", Image.DecompressionBombWarning)


class MediaError(RuntimeError):
    """A media operation could not be completed."""


# --------------------------------------------------------------------------- #
# Fetching user-provided URLs
# --------------------------------------------------------------------------- #


def _assert_public_host(hostname: str):
    try:
        infos = socket.getaddrinfo(hostname, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise MediaError(f"Could not resolve {hostname}.") from exc
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if not address.is_global:
            raise MediaError("URLs that point to private or internal networks are not allowed.")


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = parse.urlparse(newurl)
        if target.scheme != "https" or not target.hostname:
            raise MediaError("Redirects to non-HTTPS URLs are not allowed.")
        _assert_public_host(target.hostname)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_url(url: str, target: Path, max_bytes: int) -> str | None:
    """Download a public HTTPS URL to ``target``; returns the Content-Type."""
    parsed = parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise MediaError("Only https:// URLs can be imported.")
    _assert_public_host(parsed.hostname)
    opener = request.build_opener(_NoRedirect)
    req = request.Request(url, headers={"User-Agent": "ArtCraft-Studio/1.0"})
    try:
        with opener.open(req, timeout=30) as response, target.open("wb") as output:
            declared = response.headers.get("Content-Length")
            if declared and int(declared) > max_bytes:
                raise MediaError(f"The file is larger than the {max_bytes // 1_000_000} MB limit.")
            total = 0
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > max_bytes:
                    raise MediaError(f"The file is larger than the {max_bytes // 1_000_000} MB limit.")
                output.write(chunk)
            return response.headers.get_content_type()
    except error.HTTPError as exc:
        raise MediaError(f"The URL returned HTTP {exc.code}.") from exc
    except (error.URLError, TimeoutError) as exc:
        raise MediaError("The URL could not be downloaded.") from exc


# --------------------------------------------------------------------------- #
# Video and audio (FFmpeg)
# --------------------------------------------------------------------------- #


def _run(args: list[str]):
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=FFMPEG_TIMEOUT_SECONDS, check=False
        )
    except FileNotFoundError as exc:
        raise MediaError("FFmpeg is not installed on this server.") from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaError("Processing took too long and was stopped.") from exc
    if result.returncode != 0:
        detail = result.stderr.strip().splitlines()[-1:] or ["unknown error"]
        raise MediaError(f"FFmpeg failed: {detail[0][:300]}")
    return result.stdout


def probe(path: Path) -> dict:
    output = _run(
        [FFPROBE, "-v", "error", *FFMPEG_INPUT_GUARD, "-print_format", "json",
         "-show_format", "-show_streams", str(path)]
    )
    data = json.loads(output or "{}")
    fmt = data.get("format", {})
    streams = []
    for stream in data.get("streams", []):
        streams.append(
            {
                key: stream[key]
                for key in ("codec_type", "codec_name", "width", "height", "r_frame_rate",
                            "sample_rate", "channels", "duration")
                if key in stream
            }
        )
    return {
        "duration_seconds": float(fmt["duration"]) if fmt.get("duration") else None,
        "format": fmt.get("format_name"),
        "bit_rate": int(fmt["bit_rate"]) if fmt.get("bit_rate") else None,
        "streams": streams,
    }


def _has_audio(path: Path) -> bool:
    return any(s.get("codec_type") == "audio" for s in probe(path)["streams"])


def _video_encode_args(ext: str) -> list[str]:
    if ext == "webm":
        return ["-c:v", "libvpx-vp9", "-crf", "32", "-b:v", "0", "-c:a", "libopus"]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart"]


def video_trim(source: Path, output: Path, start: float, end: float | None):
    if start < 0 or (end is not None and end <= start):
        raise MediaError("Trim needs 0 <= start < end.")
    args = [FFMPEG, "-y", "-v", "error", *FFMPEG_INPUT_GUARD, "-ss", f"{start:.3f}", "-i", str(source)]
    if end is not None:
        args += ["-t", f"{end - start:.3f}"]
    _run(args + _video_encode_args(output.suffix[1:]) + [str(output)])


def video_transform(
    source: Path,
    output: Path,
    max_height: int | None = None,
    speed: float = 1.0,
    mute: bool = False,
    crop: tuple[int, int, int, int] | None = None,
    rotate: int = 0,
):
    if not 0.25 <= speed <= 4.0:
        raise MediaError("Speed must be between 0.25 and 4.0.")
    if rotate not in (0, 90, 180, 270):
        raise MediaError("Rotation must be 0, 90, 180, or 270 degrees.")
    filters = []
    if crop:
        x, y, w, h = crop
        filters.append(f"crop={w}:{h}:{x}:{y}")
    if rotate == 90:
        filters.append("transpose=1")
    elif rotate == 180:
        filters.append("transpose=1,transpose=1")
    elif rotate == 270:
        filters.append("transpose=2")
    if max_height:
        height = min(max_height, MAX_VIDEO_HEIGHT)
        filters.append(f"scale=-2:'min({height},ih)'")
    if speed != 1.0:
        filters.append(f"setpts=PTS/{speed}")
    args = [FFMPEG, "-y", "-v", "error", *FFMPEG_INPUT_GUARD, "-i", str(source)]
    if filters:
        args += ["-vf", ",".join(filters)]
    if mute or not _has_audio(source):
        args += ["-an"]
    elif speed != 1.0:
        args += ["-af", f"atempo={speed}"]
    _run(args + _video_encode_args(output.suffix[1:]) + [str(output)])


def video_concat(sources: list[Path], output: Path, width: int = 1920, height: int = 1080):
    if len(sources) < 2:
        raise MediaError("Provide at least two clips to join.")
    with_audio = all(_has_audio(source) for source in sources)
    args = [FFMPEG, "-y", "-v", "error"]
    for source in sources:
        args += [*FFMPEG_INPUT_GUARD, "-i", str(source)]
    parts, labels = [], []
    for index in range(len(sources)):
        parts.append(
            f"[{index}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30[v{index}]"
        )
        labels.append(f"[v{index}]" + (f"[{index}:a]" if with_audio else ""))
    audio_flag = 1 if with_audio else 0
    graph = ";".join(parts) + ";" + "".join(labels) + f"concat=n={len(sources)}:v=1:a={audio_flag}[v]" + (
        "[a]" if with_audio else ""
    )
    args += ["-filter_complex", graph, "-map", "[v]"]
    if with_audio:
        args += ["-map", "[a]"]
    _run(args + _video_encode_args(output.suffix[1:]) + [str(output)])


def video_add_audio(video: Path, audio: Path, output: Path, replace: bool, music_volume: float):
    args = [FFMPEG, "-y", "-v", "error", *FFMPEG_INPUT_GUARD, "-i", str(video),
            *FFMPEG_INPUT_GUARD, "-i", str(audio)]
    if replace or not _has_audio(video):
        args += ["-map", "0:v", "-map", "1:a", "-shortest"]
    else:
        args += ["-filter_complex",
                 f"[1:a]volume={music_volume}[m];[0:a][m]amix=inputs=2:duration=first[a]",
                 "-map", "0:v", "-map", "[a]"]
    _run(args + _video_encode_args(output.suffix[1:]) + [str(output)])


def video_add_text(video: Path, output: Path, text: str, position: str, font_size: int,
                   start: float | None, end: float | None):
    positions = {
        "top": "x=(w-text_w)/2:y=h*0.06",
        "center": "x=(w-text_w)/2:y=(h-text_h)/2",
        "bottom": "x=(w-text_w)/2:y=h*0.88-text_h",
    }
    if position not in positions:
        raise MediaError("Position must be top, center, or bottom.")
    # textfile avoids every drawtext escaping pitfall for user-supplied captions.
    text_file = output.with_suffix(".txt")
    text_file.write_text(text[:500], encoding="utf-8")
    enable = ""
    if start is not None or end is not None:
        enable = f":enable='between(t,{start or 0},{end if end is not None else 1e9})'"
    drawtext = (
        f"drawtext=textfile='{text_file}':fontcolor=white:fontsize={font_size}:"
        f"box=1:boxcolor=black@0.45:boxborderw=18:{positions[position]}{enable}"
    )
    args = [FFMPEG, "-y", "-v", "error", *FFMPEG_INPUT_GUARD, "-i", str(video), "-vf", drawtext]
    _run(args + _video_encode_args(output.suffix[1:]) + [str(output)])


def extract_audio(video: Path, output: Path):
    codec = {"mp3": ["-c:a", "libmp3lame", "-q:a", "2"], "wav": ["-c:a", "pcm_s16le"],
             "m4a": ["-c:a", "aac", "-b:a", "192k"]}[output.suffix[1:]]
    _run([FFMPEG, "-y", "-v", "error", *FFMPEG_INPUT_GUARD, "-i", str(video), "-vn", *codec, str(output)])


def video_frame(video: Path, output: Path, at_seconds: float):
    _run([FFMPEG, "-y", "-v", "error", *FFMPEG_INPUT_GUARD, "-ss", f"{max(at_seconds, 0):.3f}",
          "-i", str(video), "-frames:v", "1", str(output)])


# --------------------------------------------------------------------------- #
# Images (Pillow)
# --------------------------------------------------------------------------- #

IMAGE_OPERATIONS = (
    "crop, resize, rotate, flip_horizontal, flip_vertical, brightness, contrast, "
    "saturation, sharpness, blur, sharpen, grayscale, sepia, invert, auto_contrast, "
    "border, text"
)


def _open_image(path: Path) -> Image.Image:
    try:
        image = Image.open(path)
        image.load()
    except Image.DecompressionBombError as exc:
        raise MediaError("The image is too large to process.") from exc
    except (OSError, Image.DecompressionBombWarning) as exc:
        raise MediaError("The file is not a readable image.") from exc
    return ImageOps.exif_transpose(image)


def _font(size: int):
    for candidate in ("DejaVuSans.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf"):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def _apply_image_operation(image: Image.Image, op: dict) -> Image.Image:
    name = op.get("op")
    if name == "crop":
        box = (int(op["left"]), int(op["top"]), int(op["right"]), int(op["bottom"]))
        if not (0 <= box[0] < box[2] <= image.width and 0 <= box[1] < box[3] <= image.height):
            raise MediaError(f"Crop box is outside the {image.width}x{image.height} image.")
        return image.crop(box)
    if name == "resize":
        width, height = op.get("width"), op.get("height")
        if not width and not height:
            raise MediaError("Resize needs width, height, or both.")
        width = int(width or image.width * int(height) / image.height)
        height = int(height or image.height * int(width) / image.width)
        if width * height > MAX_IMAGE_PIXELS:
            raise MediaError("The requested size is too large.")
        return image.resize((max(width, 1), max(height, 1)), Image.Resampling.LANCZOS)
    if name == "rotate":
        return image.rotate(-float(op["degrees"]), expand=True, resample=Image.Resampling.BICUBIC)
    if name == "flip_horizontal":
        return ImageOps.mirror(image)
    if name == "flip_vertical":
        return ImageOps.flip(image)
    enhancers = {"brightness": ImageEnhance.Brightness, "contrast": ImageEnhance.Contrast,
                 "saturation": ImageEnhance.Color, "sharpness": ImageEnhance.Sharpness}
    if name in enhancers:
        factor = float(op.get("factor", 1.0))
        if not 0 <= factor <= 5:
            raise MediaError(f"{name} factor must be between 0 and 5.")
        base = image.convert("RGBA") if image.mode not in ("RGB", "RGBA", "L") else image
        return enhancers[name](base).enhance(factor)
    if name == "blur":
        return image.filter(ImageFilter.GaussianBlur(min(float(op.get("radius", 2)), 50)))
    if name == "sharpen":
        return image.filter(ImageFilter.UnsharpMask(radius=2, percent=int(op.get("percent", 150))))
    if name == "grayscale":
        return ImageOps.grayscale(image)
    if name == "sepia":
        gray = ImageOps.grayscale(image)
        return ImageOps.colorize(gray, "#2e1f0f", "#f4e3c1")
    if name == "invert":
        return ImageOps.invert(image.convert("RGB"))
    if name == "auto_contrast":
        return ImageOps.autocontrast(image.convert("RGB"))
    if name == "border":
        return ImageOps.expand(image, border=int(op.get("size", 20)), fill=op.get("color", "white"))
    if name == "text":
        canvas = image.convert("RGBA")
        overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        size = int(op.get("size", max(canvas.width // 20, 16)))
        draw.text(
            (int(op.get("x", 20)), int(op.get("y", 20))),
            str(op["text"])[:500],
            fill=op.get("color", "white"),
            font=_font(size),
            stroke_width=max(size // 15, 1),
            stroke_fill=op.get("stroke_color", "black"),
        )
        return Image.alpha_composite(canvas, overlay)
    raise MediaError(f"Unknown image operation '{name}'. Available: {IMAGE_OPERATIONS}.")


def image_edit(source: Path, output: Path, operations: list[dict], quality: int = 90):
    if not operations:
        raise MediaError("Provide at least one image operation.")
    if len(operations) > 30:
        raise MediaError("At most 30 operations per edit.")
    image = _open_image(source)
    for op in operations:
        try:
            image = _apply_image_operation(image, op)
        except (KeyError, TypeError, ValueError) as exc:
            raise MediaError(f"Invalid parameters for operation {op!r}.") from exc
    _save_image(image, output, quality)


def _save_image(image: Image.Image, output: Path, quality: int = 90):
    fmt = {"jpg": "JPEG", "jpeg": "JPEG", "png": "PNG", "webp": "WEBP", "gif": "GIF",
           "bmp": "BMP", "tiff": "TIFF"}[output.suffix[1:]]
    if fmt in ("JPEG", "BMP") and image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    options = {"quality": max(1, min(quality, 100))} if fmt in ("JPEG", "WEBP") else {}
    image.save(output, fmt, **options)


def image_composite(base: Path, overlay: Path, output: Path, x: int, y: int,
                    scale: float, opacity: float):
    canvas = _open_image(base).convert("RGBA")
    layer = _open_image(overlay).convert("RGBA")
    if scale != 1.0:
        layer = layer.resize((max(int(layer.width * scale), 1), max(int(layer.height * scale), 1)))
    if opacity < 1.0:
        alpha = layer.getchannel("A").point(lambda value: int(value * max(opacity, 0)))
        layer.putalpha(alpha)
    canvas.alpha_composite(layer, (x, y))
    _save_image(canvas, output)


# --------------------------------------------------------------------------- #
# PDFs (pypdf + ReportLab)
# --------------------------------------------------------------------------- #


def _reader(path: Path):
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(path)
        if reader.is_encrypted:
            raise MediaError("Password-protected PDFs are not supported.")
        _ = len(reader.pages)
        return reader
    except PdfReadError as exc:
        raise MediaError("The file is not a readable PDF.") from exc


def _page_indexes(spec: list[int], count: int) -> list[int]:
    indexes = []
    for number in spec:
        if not 1 <= number <= count:
            raise MediaError(f"Page {number} does not exist; the PDF has {count} pages.")
        indexes.append(number - 1)
    return indexes


def pdf_info(path: Path) -> dict:
    reader = _reader(path)
    metadata = reader.metadata or {}
    first = reader.pages[0].mediabox if reader.pages else None
    return {
        "page_count": len(reader.pages),
        "title": metadata.get("/Title"),
        "author": metadata.get("/Author"),
        "first_page_size_points": [float(first.width), float(first.height)] if first else None,
        "has_form_fields": bool(reader.get_fields()),
        "text_preview": (reader.pages[0].extract_text() or "")[:1500] if reader.pages else "",
    }


def pdf_extract_text(path: Path, pages: list[int] | None, max_chars: int = 50_000) -> str:
    reader = _reader(path)
    indexes = _page_indexes(pages, len(reader.pages)) if pages else range(len(reader.pages))
    chunks = [f"--- Page {i + 1} ---\n{reader.pages[i].extract_text() or ''}" for i in indexes]
    return "\n".join(chunks)[:max_chars]


def pdf_organize(path: Path, output: Path, pages: list[int], rotate: dict[int, int] | None = None):
    """Build a new PDF from ``pages`` (1-based, in order; repeat or omit freely)."""
    from pypdf import PdfWriter

    reader = _reader(path)
    writer = PdfWriter()
    rotate = rotate or {}
    for index in _page_indexes(pages, len(reader.pages)):
        page = writer.add_page(reader.pages[index])
        degrees = int(rotate.get(index + 1, rotate.get(str(index + 1), 0)))
        if degrees % 90:
            raise MediaError("Page rotation must be a multiple of 90 degrees.")
        if degrees:
            page.rotate(degrees)
    with output.open("wb") as handle:
        writer.write(handle)


def pdf_merge(sources: list[Path], output: Path):
    from pypdf import PdfWriter

    if len(sources) < 2:
        raise MediaError("Provide at least two PDFs to merge.")
    writer = PdfWriter()
    for source in sources:
        writer.append(_reader(source))
    with output.open("wb") as handle:
        writer.write(handle)


def pdf_fill_form(path: Path, output: Path, values: dict[str, str]):
    from pypdf import PdfWriter

    reader = _reader(path)
    fields = reader.get_fields() or {}
    unknown = sorted(set(values) - set(fields))
    if unknown:
        raise MediaError(f"Unknown form fields: {', '.join(unknown)}. Available: {', '.join(sorted(fields))}.")
    writer = PdfWriter(clone_from=reader)
    for page in writer.pages:
        writer.update_page_form_field_values(page, values, auto_regenerate=False)
    writer.set_need_appearances_writer(True)
    with output.open("wb") as handle:
        writer.write(handle)


def _overlay_page(width: float, height: float, draw) -> "object":
    from pypdf import PdfReader
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(width, height))
    draw(pdf)
    pdf.save()
    buffer.seek(0)
    return PdfReader(buffer).pages[0]


def pdf_add_text(path: Path, output: Path, page: int, text: str, x: float, y: float,
                 font_size: float = 12, color: str = "#000000"):
    """Write text onto a page. ``x``/``y`` are fractions of the page from the top-left."""
    from pypdf import PdfWriter
    from reportlab.lib.colors import HexColor

    reader = _reader(path)
    (index,) = _page_indexes([page], len(reader.pages))
    writer = PdfWriter(clone_from=reader)
    target = writer.pages[index]
    width, height = float(target.mediabox.width), float(target.mediabox.height)

    def draw(pdf):
        pdf.setFillColor(HexColor(color))
        pdf.setFont("Helvetica", font_size)
        for line_number, line in enumerate(text[:2000].splitlines() or [""]):
            pdf.drawString(x * width, height - y * height - font_size * (line_number + 1), line)

    target.merge_page(_overlay_page(width, height, draw))
    with output.open("wb") as handle:
        writer.write(handle)


def pdf_watermark(path: Path, output: Path, text: str, opacity: float = 0.15):
    from pypdf import PdfWriter

    reader = _reader(path)
    writer = PdfWriter(clone_from=reader)
    for page in writer.pages:
        width, height = float(page.mediabox.width), float(page.mediabox.height)

        def draw(pdf, width=width, height=height):
            pdf.setFillAlpha(max(0.02, min(opacity, 1)))
            pdf.setFont("Helvetica-Bold", min(width, height) / 10)
            pdf.translate(width / 2, height / 2)
            pdf.rotate(35)
            pdf.drawCentredString(0, 0, text[:80])

        page.merge_page(_overlay_page(width, height, draw))
    with output.open("wb") as handle:
        writer.write(handle)


SIGNATURE_POSITIONS = {
    "bottom-right": (0.58, 0.04),
    "bottom-left": (0.06, 0.04),
    "bottom-center": (0.32, 0.04),
    "top-right": (0.58, 0.84),
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pdf_sign(
    path: Path,
    output: Path,
    signer_name: str,
    signer_account: str,
    page: int | None = None,
    position: str = "bottom-right",
    signature_image: Path | None = None,
    reason: str | None = None,
) -> dict:
    """Apply a visible electronic signature and append a signed audit-trail page."""
    from pypdf import PdfWriter
    from reportlab.lib.colors import HexColor
    from reportlab.lib.utils import ImageReader

    if position not in SIGNATURE_POSITIONS:
        raise MediaError(f"Position must be one of: {', '.join(SIGNATURE_POSITIONS)}.")
    signer_name = signer_name.strip()[:120]
    if not signer_name:
        raise MediaError("A signer name is required.")
    reader = _reader(path)
    (index,) = _page_indexes([page or len(reader.pages)], len(reader.pages))
    original_hash = sha256_file(path)
    signed_at = datetime.now(timezone.utc).replace(microsecond=0)
    writer = PdfWriter(clone_from=reader)
    target = writer.pages[index]
    width, height = float(target.mediabox.width), float(target.mediabox.height)
    box_x, box_y = SIGNATURE_POSITIONS[position]
    box_w, box_h = min(width * 0.36, 240), 70

    image_reader = None
    if signature_image is not None:
        stamp = _open_image(signature_image).convert("RGBA")
        stamp.thumbnail((800, 300))
        buffer = io.BytesIO()
        stamp.save(buffer, "PNG")
        buffer.seek(0)
        image_reader = ImageReader(buffer)

    def draw(pdf):
        x, y = box_x * width, box_y * height
        pdf.setStrokeColor(HexColor("#1f4fd1"))
        pdf.setLineWidth(0.8)
        pdf.roundRect(x, y, box_w, box_h, 6)
        if image_reader:
            pdf.drawImage(image_reader, x + 6, y + 24, width=box_w - 12, height=box_h - 30,
                          preserveAspectRatio=True, mask="auto", anchor="w")
        else:
            pdf.setFillColor(HexColor("#0f2a80"))
            pdf.setFont("Times-Italic", 20)
            pdf.drawString(x + 10, y + 34, signer_name)
        pdf.setFillColor(HexColor("#333333"))
        pdf.setFont("Helvetica", 6.5)
        pdf.drawString(x + 8, y + 15, f"Electronically signed by {signer_name}")
        pdf.drawString(x + 8, y + 6, f"{signed_at.isoformat()} · ArtCraft Studio · ref {original_hash[:12]}")

    target.merge_page(_overlay_page(width, height, draw))

    def audit(pdf):
        pdf.setFont("Helvetica-Bold", 16)
        pdf.drawString(54, height - 72, "Electronic signature audit trail")
        pdf.setFont("Helvetica", 10)
        rows = [
            ("Signer", signer_name),
            ("Signer account (pseudonymous)", signer_account),
            ("Signed at (UTC)", signed_at.isoformat()),
            ("Signature placed on page", str(index + 1)),
            ("Reason", (reason or "Not stated")[:200]),
            ("Original document SHA-256", original_hash),
            ("Signing service", "ArtCraft Studio (open source)"),
        ]
        line_y = height - 110
        for label, value in rows:
            pdf.setFont("Helvetica-Bold", 9)
            pdf.drawString(54, line_y, label)
            pdf.setFont("Helvetica", 9)
            pdf.drawString(230, line_y, value)
            line_y -= 20
        pdf.setFont("Helvetica-Oblique", 8)
        pdf.drawString(54, line_y - 10, "The signer authenticated with their ArtCraft Studio account and "
                       "explicitly asked to sign this document.")

    writer.add_page(_overlay_page(width, height, audit))
    writer.add_metadata({"/ArtCraftSignedBy": signer_name, "/ArtCraftSignedAt": signed_at.isoformat(),
                         "/ArtCraftOriginalSHA256": original_hash})
    with output.open("wb") as handle:
        writer.write(handle)
    return {
        "signer_name": signer_name,
        "signed_at": signed_at.isoformat(),
        "original_sha256": original_hash,
        "signed_sha256": sha256_file(output),
    }
