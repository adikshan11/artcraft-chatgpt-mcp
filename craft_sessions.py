# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Adithya Shankaran

"""Hosted Craft app sessions for ArtCraft Studio.

Each session runs one copy of a Craft app's MCP server (`<app>-cli mcp`) for one
signed-in user, in a private temporary folder. The app is started inside that
folder with --automation-read-root and --automation-write-root set to it, so it
refuses every path outside (checked by hand for each app in craft_apps.json).
The child process gets a minimal environment, so it never sees the server's
storage keys or OAuth settings, plus memory, file-size and time limits.

Sessions live in this process's memory, so Studio must run as a single worker.
"""

import asyncio
import json
import os
import re
import secrets
import signal
import tempfile
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

MANIFEST_PATH = Path(__file__).with_name("craft_apps.json")
IDLE_SECONDS = int(os.environ.get("CRAFT_IDLE_MINUTES", "10")) * 60
CALL_TIMEOUT_SECONDS = int(os.environ.get("CRAFT_CALL_TIMEOUT_SECONDS", "120"))
START_TIMEOUT_SECONDS = 20
MAX_SESSIONS = int(os.environ.get("CRAFT_MAX_SESSIONS", "6"))
MAX_SESSIONS_PER_USER = int(os.environ.get("CRAFT_MAX_SESSIONS_PER_USER", "2"))
MEMORY_LIMIT_BYTES = int(os.environ.get("CRAFT_MEMORY_LIMIT_MB", "1536")) * 1_000_000
MAX_FILE_BYTES = int(os.environ.get("STUDIO_MAX_FILE_MB", "500")) * 1_000_000
MAX_LINE_BYTES = 64 * 1024 * 1024
PROTOCOL_VERSION = "2025-06-18"
SESSION_ID_PREFIX = "craft_"


class CraftError(RuntimeError):
    """A user-facing problem with a Craft app or session."""


@dataclass(frozen=True)
class CraftApp:
    name: str
    title: str
    version: str
    description: str
    website: str
    extensions: tuple[str, ...]
    hidden_tools: frozenset[str]
    mcp_args: tuple[str, ...] = ()
    # Engine commands refused even through the app's generic command tools. An entry
    # ending in "." blocks every command with that prefix, e.g. "collab.".
    blocked_commands: tuple[str, ...] = ()

    @property
    def cli(self) -> Path:
        return apps_dir() / self.name / f"{self.name}-cli"

    @property
    def installed(self) -> bool:
        return self.cli.is_file() and os.access(self.cli, os.X_OK)


def apps_dir() -> Path:
    return Path(os.environ.get("CRAFT_APPS_DIR", "/opt/craft"))


@lru_cache(maxsize=1)
def load_apps() -> dict[str, CraftApp]:
    apps = {}
    for entry in json.loads(MANIFEST_PATH.read_text())["apps"]:
        apps[entry["name"]] = CraftApp(
            name=entry["name"],
            title=entry["title"],
            version=entry["version"],
            description=entry["description"],
            website=entry["website"],
            extensions=tuple(entry["extensions"]),
            hidden_tools=frozenset(entry.get("hidden_tools", ())),
            mcp_args=tuple(entry.get("mcp_args", ())),
            blocked_commands=tuple(entry.get("blocked_commands", ())),
        )
    return apps


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def blocked_command(app: CraftApp, arguments) -> str | None:
    """The first blocked engine command named anywhere in a tool call's arguments."""
    if not app.blocked_commands:
        return None
    for text in _strings(arguments):
        # Undo \\uXXXX escapes so a command id can't be hidden inside embedded JSON.
        text = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), text)
        for blocked in app.blocked_commands:
            tail = r"[A-Za-z_]" if blocked.endswith(".") else r"(?![\w.])"
            if re.search(r"(?<![\w.])" + re.escape(blocked) + tail, text):
                return blocked.rstrip(".") + ("…" if blocked.endswith(".") else "")
    return None


def installed_apps() -> list[CraftApp]:
    return [app for app in load_apps().values() if app.installed]


def get_app(name: str) -> CraftApp:
    app = load_apps().get((name or "").strip().lower())
    if app is None or not app.installed:
        available = ", ".join(a.name for a in installed_apps()) or "none"
        raise CraftError(f"Craft app '{name}' is not available. Available apps: {available}.")
    return app


def _limit_child() -> None:
    """Runs in the child before exec: cap memory, file size and core dumps."""
    import resource

    for limit, value in (
        (resource.RLIMIT_AS, MEMORY_LIMIT_BYTES),
        (resource.RLIMIT_FSIZE, MAX_FILE_BYTES),
        (resource.RLIMIT_CORE, 0),
    ):
        try:
            resource.setrlimit(limit, (value, value))
        except (ValueError, OSError):
            pass  # not every platform supports every limit (e.g. RLIMIT_AS on macOS)


def _child_env(home: Path) -> dict[str, str]:
    # Built from scratch, never copied from os.environ, so server secrets stay out.
    return {
        "HOME": str(home),
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "TMPDIR": str(home / "tmp"),
        "XDG_CONFIG_HOME": str(home / "config"),
        "XDG_CACHE_HOME": str(home / "cache"),
        "XDG_DATA_HOME": str(home / "data"),
    }


class _StdioClient:
    """A small MCP client for one Craft CLI over newline-delimited JSON-RPC."""

    def __init__(self, process: asyncio.subprocess.Process):
        self.process = process
        self._next_id = 0
        self._lock = asyncio.Lock()
        self._stderr_tail = b""
        self._stderr_task = asyncio.ensure_future(self._drain_stderr())

    @classmethod
    async def start(cls, cli: Path, root: Path, home: Path, extra_args=()) -> "_StdioClient":
        for sub in ("tmp", "config", "cache", "data"):
            (home / sub).mkdir(parents=True, exist_ok=True)
        process = await asyncio.create_subprocess_exec(
            str(cli), "mcp", *extra_args,
            "--automation-read-root", str(root),
            "--automation-write-root", str(root),
            cwd=str(root),  # relative paths resolve inside the root
            env=_child_env(home),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            preexec_fn=_limit_child,
            start_new_session=True,
            limit=MAX_LINE_BYTES,
        )
        return cls(process)

    async def _drain_stderr(self):
        while chunk := await self.process.stderr.read(4096):
            self._stderr_tail = (self._stderr_tail + chunk)[-2000:]

    @property
    def stderr_tail(self) -> str:
        return self._stderr_tail.decode("utf-8", "replace").strip()

    async def _send(self, message: dict):
        self.process.stdin.write(json.dumps(message).encode() + b"\n")
        await self.process.stdin.drain()

    async def request(self, method: str, params: dict | None = None) -> dict:
        async with self._lock:
            self._next_id += 1
            request_id = self._next_id
            message = {"jsonrpc": "2.0", "id": request_id, "method": method}
            if params is not None:
                message["params"] = params
            await self._send(message)
            while True:
                line = await self.process.stdout.readline()
                if not line:
                    raise CraftError("The Craft app stopped unexpectedly. " + self.stderr_tail[-300:])
                try:
                    reply = json.loads(line)
                except json.JSONDecodeError:
                    continue  # not protocol output; ignore it
                if "method" in reply:
                    if "id" in reply:  # a request from the app to us
                        answer = {"jsonrpc": "2.0", "id": reply["id"]}
                        if reply["method"] == "ping":
                            answer["result"] = {}
                        else:
                            answer["error"] = {"code": -32601, "message": "Not supported by Studio"}
                        await self._send(answer)
                    continue  # notifications are ignored
                if reply.get("id") != request_id:
                    continue
                if "error" in reply:
                    raise CraftError(f"Craft app error: {reply['error'].get('message', reply['error'])}")
                return reply.get("result") or {}

    async def notify(self, method: str):
        await self._send({"jsonrpc": "2.0", "method": method})

    async def kill(self):
        if self.process.returncode is None:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            await self.process.wait()
        self._stderr_task.cancel()


@dataclass
class CraftSession:
    id: str
    user_id: str
    app: CraftApp
    folder: tempfile.TemporaryDirectory
    client: _StdioClient | None = None
    tools: list[dict] = field(default_factory=list)
    last_used: float = field(default_factory=time.monotonic)
    snapshot: dict[str, tuple[int, int]] = field(default_factory=dict)

    @property
    def root(self) -> Path:
        return Path(self.folder.name) / "files"

    @property
    def home(self) -> Path:
        return Path(self.folder.name) / "home"

    def files(self) -> dict[str, Path]:
        """Regular files under the root, by relative path. Symlinks are never followed or returned."""
        found = {}
        root = self.root.resolve()
        for directory, subdirs, names in os.walk(root, followlinks=False):
            subdirs[:] = [d for d in subdirs if not (Path(directory) / d).is_symlink()]
            for name in names:
                path = Path(directory) / name
                if path.is_symlink() or not path.is_file():
                    continue
                if not path.resolve().is_relative_to(root):
                    continue
                found[path.relative_to(root).as_posix()] = path
        return found

    def take_snapshot(self):
        self.snapshot = {rel: _signature(path) for rel, path in self.files().items()}

    def changed_files(self) -> dict[str, Path]:
        return {rel: path for rel, path in self.files().items() if self.snapshot.get(rel) != _signature(path)}


def _signature(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns


class SessionManager:
    def __init__(self):
        self.sessions: dict[str, CraftSession] = {}
        self._lock = asyncio.Lock()

    async def reap_idle(self):
        cutoff = time.monotonic() - IDLE_SECONDS
        for session in [s for s in self.sessions.values() if s.last_used < cutoff]:
            await self._close(session)

    def user_sessions(self, user_id: str) -> list[CraftSession]:
        return [s for s in self.sessions.values() if s.user_id == user_id]

    async def open(self, user_id: str, app: CraftApp, populate) -> CraftSession:
        """Create a session folder, let `populate(root)` copy the user's files in, then start the app."""
        await self.reap_idle()
        async with self._lock:
            mine = self.user_sessions(user_id)
            if len(mine) >= MAX_SESSIONS_PER_USER:
                open_ids = ", ".join(s.id for s in mine)
                raise CraftError(
                    f"You already have {len(mine)} open Craft sessions ({open_ids}). "
                    "Save and close one with close_craft_session first."
                )
            if len(self.sessions) >= MAX_SESSIONS:
                raise CraftError("Studio is running as many Craft sessions as it can. Try again in a few minutes.")
            session = CraftSession(
                id=SESSION_ID_PREFIX + secrets.token_hex(12),
                user_id=user_id,
                app=app,
                folder=tempfile.TemporaryDirectory(prefix="craft-"),
            )
            self.sessions[session.id] = session
        try:
            session.root.mkdir()
            session.home.mkdir()
            await asyncio.to_thread(populate, session.root)
            session.take_snapshot()
            session.client = await _StdioClient.start(app.cli, session.root, session.home, app.mcp_args)
            await asyncio.wait_for(self._initialize(session), START_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            await self._close(session)
            raise CraftError(f"{app.title} did not start in time.")
        except BaseException:
            await self._close(session)
            raise
        return session

    async def _initialize(self, session: CraftSession):
        client = session.client
        await client.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "artcraft-studio", "version": "1.0.0"},
        })
        await client.notify("notifications/initialized")
        tools, cursor = [], None
        while True:
            result = await client.request("tools/list", {"cursor": cursor} if cursor else {})
            tools.extend(result.get("tools", []))
            cursor = result.get("nextCursor")
            if not cursor:
                break
        session.tools = [t for t in tools if t.get("name") not in session.app.hidden_tools]

    def get(self, user_id: str, session_id: str) -> CraftSession:
        session = self.sessions.get(session_id or "")
        if session is None or session.user_id != user_id:
            # Same message either way, so session ids of other users are not confirmed.
            raise CraftError(f"Craft session '{session_id}' was not found. It may have closed after being idle.")
        session.last_used = time.monotonic()
        return session

    async def call(self, session: CraftSession, tool: str, arguments: dict) -> dict:
        if tool not in {t["name"] for t in session.tools}:
            raise CraftError(f"{session.app.title} has no tool '{tool}' here. Use one from open_craft_session's list.")
        if command := blocked_command(session.app, arguments):
            raise CraftError(
                f"{session.app.title}'s `{command}` command is turned off in Studio, because it reaches outside "
                "the session (network sharing, plug-ins or folders elsewhere on the server)."
            )
        try:
            result = await asyncio.wait_for(
                session.client.request("tools/call", {"name": tool, "arguments": arguments or {}}),
                CALL_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            await self._close(session)
            raise CraftError(
                f"{session.app.title} took longer than {CALL_TIMEOUT_SECONDS} seconds, so the session was closed. "
                "Unsaved changes are lost."
            )
        except CraftError:
            if session.client.process.returncode is not None:
                await self._close(session)
            raise
        session.last_used = time.monotonic()
        return result

    async def close(self, user_id: str, session_id: str):
        await self._close(self.get(user_id, session_id))

    async def _close(self, session: CraftSession):
        self.sessions.pop(session.id, None)
        if session.client is not None:
            await session.client.kill()
        session.folder.cleanup()

    async def close_user(self, user_id: str):
        for session in list(self.user_sessions(user_id)):
            await self._close(session)

    def close_user_now(self, user_id: str) -> None:
        """Stop this user's Craft apps without waiting on the server's event loop.

        Disconnect and account close run from a request thread. The session lock
        belongs to the server loop, so this only removes the sessions and kills
        the processes.
        """
        for session in list(self.user_sessions(user_id)):
            self.sessions.pop(session.id, None)
            client = session.client
            if client is not None and client.process.returncode is None:
                try:
                    os.killpg(client.process.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError, OSError):
                    pass
            try:
                session.folder.cleanup()
            except OSError:
                pass

    async def close_all(self):
        for session in list(self.sessions.values()):
            await self._close(session)


manager = SessionManager()
