# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Adithya Shankaran

"""A stand-in Craft CLI for tests: `fake_craft_cli.py mcp --automation-read-root R --automation-write-root W`.

Speaks newline-delimited MCP over stdio and confines file paths to the roots the
way the real apps do (relative paths resolve from the working directory; anything
that resolves outside the root, including through symlinks, is refused).
"""

import base64
import json
import os
import sys
import time
from pathlib import Path

args = sys.argv[1:]
READ_ROOT = Path(args[args.index("--automation-read-root") + 1]).resolve()
WRITE_ROOT = Path(args[args.index("--automation-write-root") + 1]).resolve()
PNG_1x1 = base64.b64encode(bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
)).decode()

TOOLS = [
    {"name": "write_file", "description": "Write text to a file.", "inputSchema": {"type": "object"}},
    {"name": "read_file", "description": "Read a text file.", "inputSchema": {"type": "object"}},
    {"name": "env", "description": "Show the environment.", "inputSchema": {"type": "object"}},
    {"name": "cwd", "description": "Show the working directory.", "inputSchema": {"type": "object"}},
    {"name": "argv", "description": "Show the command-line arguments. " + "Long description. " * 20,
     "inputSchema": {"type": "object", "properties": {"verbose": {"type": "boolean"}}}},
    {"name": "image", "description": "Return a PNG.", "inputSchema": {"type": "object"}},
    {"name": "big_text", "description": "Return a lot of text.", "inputSchema": {"type": "object"}},
    {"name": "sleep", "description": "Sleep.", "inputSchema": {"type": "object"}},
    {"name": "crash", "description": "Exit.", "inputSchema": {"type": "object"}},
    {"name": "screenshot", "description": "Desktop only.", "inputSchema": {"type": "object"}},
]


def confined(path: str, root: Path, kind: str) -> Path:
    resolved = (Path.cwd() / path).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"automation path rejected: outside the {kind} root {root}: {path}")
    return resolved


def text(value, error=False):
    return {"content": [{"type": "text", "text": value}], "isError": error}


def call(name, a):
    try:
        if name == "write_file":
            confined(a["path"], WRITE_ROOT, "write").write_text(a.get("text", ""))
            return text(json.dumps({"saved": True, "path": a["path"]}))
        if name == "read_file":
            return text(confined(a["path"], READ_ROOT, "read").read_text())
        if name == "env":
            return text(json.dumps(dict(os.environ)))
        if name == "cwd":
            return text(os.getcwd())
        if name == "argv":
            return text(json.dumps(sys.argv[1:]))
        if name == "image":
            return {"content": [{"type": "image", "data": PNG_1x1, "mimeType": "image/png"}]}
        if name == "big_text":
            return text("x" * 200_000)
        if name == "sleep":
            time.sleep(a.get("seconds", 5))
            return text("slept")
        if name == "crash":
            os._exit(3)
        return text(f"unknown tool {name}", error=True)
    except (ValueError, OSError) as exc:
        return text(str(exc), error=True)


def send(message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


for line in sys.stdin:
    message = json.loads(line)
    method, request_id = message.get("method"), message.get("id")
    if request_id is None:
        continue
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": request_id, "result": {
            "protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
            "serverInfo": {"name": "fakecraft", "version": "0.0.1"}}})
        # A server-to-client request in the middle of the stream, which Studio must answer.
        send({"jsonrpc": "2.0", "id": "srv-1", "method": "ping"})
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}})
    elif method == "tools/call":
        params = message["params"]
        send({"jsonrpc": "2.0", "id": request_id, "result": call(params["name"], params.get("arguments", {}))})
    else:
        send({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "unknown"}})
