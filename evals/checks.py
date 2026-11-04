# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Adithya Shankaran

"""Look inside a file a Craft app made. Each check raises CheckFailed with what it found."""

import re
import wave
import zipfile
from array import array
from pathlib import Path

from PIL import Image, ImageStat, UnidentifiedImageError
from pypdf import PdfReader
from pypdf.errors import PyPdfError


class CheckFailed(Exception):
    pass


def _contains(found: str, text: str) -> bool:
    return " ".join(text.split()) in " ".join(found.split())


def pdf(path: Path, pages: int | None = None, text: str | None = None):
    try:
        reader = PdfReader(path)
        count = len(reader.pages)
        found = " ".join(page.extract_text() or "" for page in reader.pages) if text else ""
    except (PyPdfError, ValueError, OSError) as exc:
        raise CheckFailed(f"not a readable PDF: {type(exc).__name__}") from exc
    if count == 0 or (pages is not None and count != pages):
        raise CheckFailed(f"has {count} pages, expected {pages if pages is not None else 'at least 1'}")
    if text and not _contains(found, text):
        raise CheckFailed(f"doesn't contain {text!r}")


def office_text(path: Path, member_pattern: str, text: str):
    """``text`` is in the XML parts of a .docx or .pptx matching ``member_pattern``."""
    try:
        with zipfile.ZipFile(path) as archive:
            parts = [archive.read(name).decode("utf-8", "replace") for name in archive.namelist()
                     if re.fullmatch(member_pattern, name)]
    except (zipfile.BadZipFile, OSError) as exc:
        raise CheckFailed(f"not a readable Office file: {type(exc).__name__}") from exc
    if not parts:
        raise CheckFailed(f"has no part matching {member_pattern}")
    if not _contains(re.sub(r"<[^>]+>", "", " ".join(parts)), text):
        raise CheckFailed(f"doesn't contain {text!r}")


def _image(path: Path, kind: str) -> Image.Image:
    try:
        image = Image.open(path)
        image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise CheckFailed(f"not a readable image: {type(exc).__name__}") from exc
    if image.format != kind:
        raise CheckFailed(f"is {image.format}, expected {kind}")
    return image.convert("RGB")


def image(path: Path, kind: str, size: tuple[int, int] | None = None):
    """A readable image that isn't one flat colour."""
    picture = _image(path, kind)
    if size and picture.size != size:
        raise CheckFailed(f"is {picture.size}, expected {size}")
    if max(high - low for low, high in picture.getextrema()) == 0:
        raise CheckFailed("is one flat colour")


def image_colour(path: Path, kind: str, colour: tuple[int, int, int], size: tuple[int, int] | None = None, tolerance: int = 12):
    """A readable image whose average colour is ``colour``."""
    picture = _image(path, kind)
    if size and picture.size != size:
        raise CheckFailed(f"is {picture.size}, expected {size}")
    average = tuple(round(value) for value in ImageStat.Stat(picture).mean)
    if max(abs(a - b) for a, b in zip(average, colour)) > tolerance:
        raise CheckFailed(f"averages {average}, expected about {colour}")


def image_has_colour(path: Path, kind: str, colour: tuple[int, int, int], share: float = 0.01, tolerance: int = 24):
    """At least ``share`` of the image's pixels are close to ``colour``."""
    picture = _image(path, kind)
    close = sum(count for count, pixel in picture.getcolors(picture.width * picture.height)
                if max(abs(a - b) for a, b in zip(pixel, colour)) <= tolerance)
    found = close / (picture.width * picture.height)
    if found < share:
        raise CheckFailed(f"{found:.1%} of pixels are near {colour}, expected at least {share:.0%}")


def audio(path: Path, seconds: float, tolerance: float = 0.1, loudest_at_least: int = 1000):
    """A WAV of about ``seconds`` that isn't silent."""
    try:
        with wave.open(str(path), "rb") as sound:
            frames = sound.getnframes()
            rate = sound.getframerate()
            width = sound.getsampwidth()
            data = sound.readframes(frames)
    except (wave.Error, EOFError, OSError) as exc:
        raise CheckFailed(f"not a readable WAV: {type(exc).__name__}") from exc
    length = frames / rate if rate else 0
    if abs(length - seconds) > tolerance:
        raise CheckFailed(f"lasts {length:.2f} s, expected about {seconds} s")
    if width == 2:
        loudest = max((abs(sample) for sample in array("h", data)), default=0)
    elif width == 3:
        loudest = max((abs(int.from_bytes(data[i:i + 3], "little", signed=True)) >> 8 for i in range(0, len(data), 3)), default=0)
    elif width == 4:
        # 32-bit WAVs can be integer or float; a silent file is all zeros either way.
        loudest = loudest_at_least if any(data) else 0
    else:
        raise CheckFailed(f"has {width * 8}-bit samples, which this check can't read")
    if loudest < loudest_at_least:
        raise CheckFailed(f"is nearly silent (loudest sample {loudest})")


def not_empty(path: Path):
    if path.stat().st_size == 0:
        raise CheckFailed("is empty")
