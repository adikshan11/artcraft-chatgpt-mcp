# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Adithya Shankaran

"""Run one job in each hosted Craft app and check the files it makes.

tests/test_craft_real.py checks that tools succeed and files get saved. These jobs
open the results: the words are in the PDF, the photo keeps its colour, the mix isn't
silent, and a saved project opens again. They go through Studio's own tools, as a person
would, and need no network or paid service. Run them in the sandbox the weekly workflow uses:

    docker run --rm --network none --cap-drop ALL --security-opt no-new-privileges \
        --read-only --tmpfs /tmp -v "$PWD/evals:/app/evals:ro" bridgecraft \
        python -m evals.run_evals > evals.json

The report goes to stdout as JSON, and the exit status is 1 if anything failed.
"""

import asyncio
import json
import logging
import math
import os
import shutil
import struct
import sys
import tempfile
import time
import traceback
import uuid
import wave
from datetime import datetime, timezone
from pathlib import Path

from mcp import Client
from PIL import Image

import craft_sessions
import studio_mcp
from evals import checks
from media_storage import LocalStorage, user_id_for

SCHEMA = 1
JOB_TIMEOUT_SECONDS = 300
MAX_DETAIL = 300
TEXT = "Hosted Craft works"
ORANGE = (200, 120, 40)
BLUE = (40, 120, 200)
USER = "eval"


class JobFailed(Exception):
    pass


class Job:
    """One Craft app session, with the steps and checks recorded as they run."""

    def __init__(self, store: LocalStorage, app: str, folder: Path):
        self.store = store
        self.app = app
        self.folder = folder
        self.user = user_id_for("local-dev", USER)
        self.session_id = None
        self.saved: dict[str, Path] = {}
        self.results: list[dict] = []

    def record(self, name: str, passed: bool, detail: str = ""):
        row = {"name": name, "passed": passed}
        if detail:
            row["detail"] = detail[:MAX_DETAIL]
        self.results.append(row)

    async def studio(self, tool: str, **arguments):
        async with Client(studio_mcp.studio) as client:
            result = await client.call_tool(tool, arguments)
        if result.is_error:
            raise JobFailed(f"{tool}: {_text(result)}")
        return result.structured_content

    async def open(self, media_ids=()):
        info = await self.studio("open_craft_session", app=self.app, media_ids=list(media_ids))
        self.session_id = info["session_id"]
        self.record("opens", True)

    async def tool(self, tool: str, **arguments):
        async with Client(studio_mcp.studio) as client:
            result = await client.call_tool(
                "run_craft_tool", {"session_id": self.session_id, "tool": tool, "arguments": arguments})
        if result.is_error:
            raise JobFailed(f"{tool}: {_text(result)}")
        return result

    def upload(self, path: Path) -> str:
        media_id = f"{uuid.uuid4().hex}.{path.suffix[1:]}"
        self.store.put_file(self.user, media_id, path)
        return media_id

    async def save(self):
        """Save the session's files through Studio, and fetch each back from storage to read it."""
        result = await self.studio("save_craft_files", session_id=self.session_id)
        for item in result["saved"]:
            target = self.folder / item["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            self.store.get_file(self.user, item["media_id"], target)
            self.saved[item["path"]] = target

    def check(self, name: str, check, filename: str, *args, **kwargs):
        path = self.saved.get(filename)
        if path is None:
            self.record(name, False, f"{filename} wasn't saved")
            return
        try:
            check(path, *args, **kwargs)
            self.record(name, True)
        except checks.CheckFailed as exc:
            self.record(name, False, f"{filename} {exc}")

    async def reopens(self, tool: str, filename: str, **arguments):
        try:
            await self.tool(tool, path=filename, **arguments)
            self.record(f"{filename} opens again", True)
        except JobFailed as exc:
            self.record(f"{filename} opens again", False, str(exc))

    async def close(self):
        if self.session_id:
            await self.studio("close_craft_session", session_id=self.session_id)


def _text(result) -> str:
    return " ".join(block.text for block in result.content if block.type == "text")


async def wordcraft(job: Job):
    await job.open()
    await job.tool("new_document", template="blank")
    await job.tool("type_text", text=TEXT)
    await job.tool("save_document", path="letter.docx")
    await job.tool("save_document", path="letter.pdf")
    await job.save()
    job.check("the Word file has the words", checks.office_text, "letter.docx", r"word/document\.xml", TEXT)
    job.check("the PDF has the words", checks.pdf, "letter.pdf", pages=1, text=TEXT)
    await job.reopens("open_document", "letter.docx")
    found = _text(await job.tool("get_text"))
    job.record("the reopened file has the words", TEXT in found, "" if TEXT in found else "get_text didn't return them")


async def deckcraft(job: Job):
    await job.open()
    await job.tool("new_presentation", theme="Harbor")
    await job.tool("add_slide", layout="titleAndContent", title=TEXT, body="A second line")
    await job.tool("save_presentation", path="deck.pptx")
    await job.tool("export", path="deck.pdf", format="pdf")
    await job.save()
    job.check("the slides have the words", checks.office_text, "deck.pptx", r"ppt/slides/slide\d+\.xml", TEXT)
    job.check("the PDF has the words", checks.pdf, "deck.pdf", text=TEXT)
    await job.reopens("open_presentation", "deck.pptx")


async def pdfcraft(job: Job):
    await job.open()
    created = await job.tool("doc_create", **{"from": "blank", "pages": 2})
    doc = json.loads(_text(created))["doc"]
    await job.tool("doc_save", doc=doc, path="a.pdf")
    await job.tool("doc_combine", paths=["a.pdf", "a.pdf"], out="both.pdf")
    await job.save()
    job.check("the new PDF has 2 pages", checks.pdf, "a.pdf", pages=2)
    job.check("the combined PDF has 4 pages", checks.pdf, "both.pdf", pages=4)
    await job.reopens("doc_open", "both.pdf")


async def photocraft(job: Job):
    source = job.folder / "photo.png"
    Image.new("RGB", (80, 60), ORANGE).save(source)
    media_id = job.upload(source)
    await job.open([media_id])
    await job.tool("doc_open", path=media_id)
    await job.tool("doc_save", path="edit.pcraft")
    await job.tool("doc_export", path="edit.jpg", quality=85)
    await job.save()
    job.check("the export keeps the photo's size and colour", checks.image_colour, "edit.jpg", "JPEG", ORANGE, size=(80, 60))
    job.check("the project was saved", checks.not_empty, "edit.pcraft")
    await job.reopens("doc_open", "edit.pcraft")


async def vectorcraft(job: Job):
    await job.open()
    await job.tool("draw_shape", shape="ellipse", x=20, y=20, width=200, height=120, fill="#3366ff")
    await job.tool("add_text", x=40, y=200, text=TEXT)
    await job.tool("save_file", path="art.vectorcraft")
    await job.tool("export", path="art.pdf", format="pdf")
    await job.save()
    job.check("the PDF has the words", checks.pdf, "art.pdf", text=TEXT)
    job.check("the project was saved", checks.not_empty, "art.vectorcraft")
    await job.reopens("open_file", "art.vectorcraft")


async def designcraft(job: Job):
    source = job.folder / "photo.png"
    Image.new("RGB", (120, 80), BLUE).save(source)
    media_id = job.upload(source)
    await job.open([media_id])
    await job.tool("new_document", pages=1)
    await job.tool("place_image", path=media_id)
    await job.tool("save_document", path="flyer.designcraft")
    await job.tool("export_png", path="flyer.png", page=0, scale=0.25)
    await job.tool("execute", command="file.exportPdf", params={"path": "flyer.pdf"})
    await job.save()
    job.check("the page shows the placed photo", checks.image_has_colour, "flyer.png", "PNG", BLUE)
    job.check("the PDF has 1 page", checks.pdf, "flyer.pdf", pages=1)
    await job.reopens("open_document", "flyer.designcraft")


async def figcraft(job: Job):
    await job.open()
    frame = await job.tool("create_node", type="frame", x=0, y=0, width=320, height=200, name="Card")
    frame_id = json.loads(_text(frame))["id"]
    box = await job.tool("create_node", type="rectangle", parent=frame_id, x=40, y=40, width=100, height=60)
    await job.tool("set_properties", ids=[json.loads(_text(box))["id"]], props={"fill": "#2878C8"})
    await job.tool("select", ids=[frame_id])
    await job.tool("save_file", path="card.figcraft")
    await job.tool("export", path="card.png", format="png")
    await job.save()
    job.check("the export shows the coloured box", checks.image_has_colour, "card.png", "PNG", BLUE)
    job.check("the project was saved", checks.not_empty, "card.figcraft")
    await job.reopens("open_file", "card.figcraft")


async def soundcraft(job: Job):
    source = job.folder / "take.wav"
    with wave.open(str(source), "wb") as sound:
        sound.setnchannels(1)
        sound.setsampwidth(2)
        sound.setframerate(8000)
        sound.writeframes(b"".join(struct.pack("<h", int(12000 * math.sin(2 * math.pi * 440 * i / 8000))) for i in range(8000)))
    media_id = job.upload(source)
    await job.open([media_id])
    await job.tool("new_session", name="Demo")
    await job.tool("import_audio", path=media_id)
    await job.tool("bounce_mix", path="mix.wav", format="wav")
    await job.tool("save_session", path="demo.scraft")
    await job.save()
    job.check("the mix lasts 1 second and isn't silent", checks.audio, "mix.wav", seconds=1)
    job.check("the session was saved", checks.not_empty, "demo.scraft")
    await job.reopens("open_session", "demo.scraft")


async def effectcraft(job: Job):
    await job.open()
    await job.tool("open_project", new=True)
    await job.tool("execute_command", command="comp.new", params={"name": "Main", "width": 320, "height": 180, "frameRate": 30, "duration": 1})
    await job.tool("execute_command", command="layer.newText", params={"text": TEXT})
    await job.tool("render_frame", path="frame.png", inline=False)
    await job.tool("save_project", path="demo.ecproj")
    await job.save()
    job.check("the frame is a picture", checks.image, "frame.png", "PNG", (320, 180))
    job.check("the project was saved", checks.not_empty, "demo.ecproj")
    await job.reopens("open_project", "demo.ecproj")


async def texturecraft(job: Job):
    await job.open()
    await job.tool("project_new", mesh="cube", resolution=128)
    await job.tool("textures_export", dir="textures", format="png")
    await job.tool("project_save", path="demo.tcraft")
    await job.save()
    job.check("the project was saved", checks.not_empty, "demo.tcraft")
    textures = [name for name in job.saved if name.endswith(".png")]
    job.record("the textures were exported", bool(textures), "" if textures else f"saved {sorted(job.saved)}")
    await job.reopens("project_open", "demo.tcraft")


async def materialcraft(job: Job):
    await job.open()
    await job.tool("graph_new", template="bricks", size=64)
    await job.tool("outputs_export", dir="maps", format="png")
    await job.tool("package_save", path="demo.mcraft")
    await job.save()
    job.check("the package was saved", checks.not_empty, "demo.mcraft")
    maps = [name for name in job.saved if name.endswith(".png")]
    job.record("the maps were exported", bool(maps), "" if maps else f"saved {sorted(job.saved)}")
    await job.reopens("package_open", "demo.mcraft")


JOBS = {job.__name__: job for job in (wordcraft, deckcraft, pdfcraft, photocraft, vectorcraft, designcraft, figcraft, soundcraft, effectcraft, texturecraft, materialcraft)}


async def run_app(app, store: LocalStorage, jobs=None) -> dict:
    """Run one app's job. A step that fails ends the job; a check that fails doesn't."""
    run = (jobs or JOBS).get(app.name)
    folder = Path(tempfile.mkdtemp(prefix=f"eval-{app.name}-"))
    job = Job(store, app.name, folder)
    started = time.perf_counter()
    try:
        if run is None:
            raise JobFailed("no eval job for this app")
        if not app.installed:
            raise JobFailed("not installed")
        await asyncio.wait_for(run(job), JOB_TIMEOUT_SECONDS)
    except JobFailed as exc:
        job.record("job finishes", False, str(exc))
    except TimeoutError:
        job.record("job finishes", False, f"took over {JOB_TIMEOUT_SECONDS} s")
    except Exception as exc:  # noqa: BLE001 - one app's crash must not hide the others' results
        traceback.print_exc()
        job.record("job finishes", False, type(exc).__name__)
    finally:
        try:
            await job.close()
        except Exception:  # noqa: BLE001 - the session may already be gone
            traceback.print_exc()
        shutil.rmtree(folder, ignore_errors=True)
    return {
        "app": app.name,
        "title": app.title,
        "version": app.version,
        "passed": bool(job.results) and all(row["passed"] for row in job.results),
        "ms": round((time.perf_counter() - started) * 1000),
        "checks": job.results,
    }


async def run_all(apps, store: LocalStorage, jobs=None) -> dict:
    rows = [await run_app(app, store, jobs) for app in apps]
    return {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "commit": os.environ.get("EVAL_COMMIT", "")[:7],
        "passed": bool(rows) and all(row["passed"] for row in rows),
        "apps": rows,
    }


def main() -> int:
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    os.environ["STUDIO_DEV_USER"] = USER
    root = Path(tempfile.mkdtemp(prefix="eval-store-"))
    store = LocalStorage(root)
    studio_mcp.storage = lambda: store
    try:
        report = asyncio.run(run_all(craft_sessions.load_apps().values(), store))
    finally:
        shutil.rmtree(root, ignore_errors=True)
    json.dump(report, sys.stdout, indent=2)
    print()
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
