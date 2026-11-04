# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Adithya Shankaran

"""Try to reach files outside the automation roots through every tool of a Craft CLI.

Run this inside a sandbox with no network. It starts `<app>-cli mcp` with one read root
and one write root, then calls every tool that takes a path, once per escape path, with
a marker file sitting outside both roots. A call fails the check when the marker's bytes
come back, when the marker changes, or when anything new appears outside the roots.

    python escape_checks.py /path/to/wordcraft-cli --extra-arg=--headless

It prints a JSON report and exits 1 if any attempt got out.
"""

import argparse
import json
import os
import re
import select
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

MARKER = "ESCAPE-MARKER-7f3a9c1e"
TIMEOUT_SECONDS = 20
PATH_KEYS = (
    "path", "paths", "file", "files", "filename", "dir", "directory", "folder", "dest", "destination",
    "output", "out", "input", "src", "source", "target", "root", "location", "image", "font", "media",
)
# Tools that run code or reach outside the process. Each must be hidden in craft_apps.json.
RISKY = re.compile(r"(^|_)(script|scripts|eval|exec|execute|shell|plugin|macro|js|run_program|open_url|fetch|http|listen)(_|$)")
# How a Craft CLI words a refusal that came from the folder lock, as opposed to bad arguments.
ROOT_REFUSAL = re.compile(r"outside|automation|not allowed|rejected|confine|read root|write root|permission", re.I)


def is_path_key(key: str) -> bool:
    key = key.lower()
    return any(key == word or key.endswith("_" + word) or key.startswith(word + "_") for word in PATH_KEYS)


def dummy(schema: dict):
    """A value that satisfies a property's schema, for the arguments that aren't paths."""
    if "enum" in schema:
        return schema["enum"][0]
    if "default" in schema:
        return schema["default"]
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((item for item in kind if item != "null"), "string")
    if kind == "integer" or kind == "number":
        return schema.get("minimum", 1)
    if kind == "boolean":
        return False
    if kind == "array":
        return []
    if kind == "object":
        return {}
    return "x"


class Client:
    def __init__(self, cli: str, read_root: Path, write_root: Path, home: Path, extra_args: list, flag_style: str = "automation"):
        prefix = "automation-" if flag_style == "automation" else ""
        env = {
            "PATH": "/usr/bin:/bin", "HOME": str(home), "TMPDIR": str(home),
            "XDG_CONFIG_HOME": str(home / "config"), "XDG_CACHE_HOME": str(home / "cache"), "XDG_DATA_HOME": str(home / "data"),
        }
        self.process = subprocess.Popen(
            [cli, "mcp", *extra_args, f"--{prefix}read-root", str(read_root), f"--{prefix}write-root", str(write_root)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd=write_root, env=env, text=True,
        )
        self.next_id = 0

    def request(self, method: str, params: dict) -> dict:
        self.next_id += 1
        self.send({"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params})
        deadline = time.monotonic() + TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            ready, _, _ = select.select([self.process.stdout], [], [], max(0, deadline - time.monotonic()))
            if not ready:
                break
            line = self.process.stdout.readline()
            if not line:
                raise RuntimeError("the app exited")
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if message.get("id") == self.next_id:
                return message
        raise TimeoutError(method)

    def send(self, message: dict) -> None:
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def start(self) -> list:
        self.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "escape-checks", "version": "1"}})
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return self.request("tools/list", {})["result"]["tools"]

    def stop(self) -> None:
        self.process.kill()
        self.process.wait()


def new_document(client, tools) -> None:
    """Open something to work on, so a refusal can't just mean there's no document yet."""
    for tool in tools:
        if re.fullmatch(r"(new_(document|presentation|session)|doc_new|project_new|graph_new|doc_create)", tool["name"]):
            required = (tool.get("inputSchema") or {}).get("required", [])
            properties = (tool.get("inputSchema") or {}).get("properties", {})
            client.request("tools/call", {"name": tool["name"], "arguments": {key: dummy(properties[key]) for key in required}})
            return


def engine_commands(client, tools) -> tuple:
    """The run tool and every engine command with a path-like parameter, from the app's own catalogue."""
    by_name = {tool["name"]: tool for tool in tools}
    lister = next((name for name in ("list_commands", "command_list") if name in by_name), None)
    runner = next((name for name in ("execute", "execute_command", "run_command", "command_run") if name in by_name), None)
    if not lister or not runner:
        return None, None, []
    reply = client.request("tools/call", {"name": lister, "arguments": {}})
    text = "".join(part.get("text", "") for part in reply["result"].get("content", []))
    try:
        data = json.loads(text)
    except ValueError:
        # FigCraft lists one command per line: "file.export* — Label {params}".
        data = [
            {"id": line.split(" ")[0].rstrip("*"), "params": line.split(" ", 1)[1]}
            for line in text.splitlines()[1:] if " " in line
        ]
    if isinstance(data, dict):
        data = next((value for value in data.values() if isinstance(value, list)), [])
    found = []
    for command in data:
        if not isinstance(command, dict):
            continue
        params = command.get("params") or command.get("schema") or command.get("parameters") or ""
        words = set(re.findall(r"[A-Za-z_]+", json.dumps(params)))
        keys = sorted(word for word in words if is_path_key(word))
        if keys and command.get("id"):
            found.append((command["id"], keys))
    return runner, by_name[runner], found


def snapshot(folder: Path) -> dict:
    return {str(path): path.stat().st_size for path in folder.rglob("*") if path.is_file() and not path.is_symlink()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("cli")
    parser.add_argument("--extra-arg", action="append", default=[])
    parser.add_argument("--flag-style", default="automation", choices=["automation", "plain"], help="plain means --read-root, for an app that doesn't use the --automation- names yet.")
    parser.add_argument("--hidden", default="", help="Comma-separated tools Studio hides, which are not called.")
    args = parser.parse_args()
    hidden = set(filter(None, args.hidden.split(",")))

    work = Path(tempfile.mkdtemp(dir=os.environ.get("ESCAPE_WORK")))
    read_root, write_root, outside, home = (work / name for name in ("read", "write", "outside", "home"))
    for folder in (read_root, write_root, outside, home):
        folder.mkdir()
    secret = outside / "secret.txt"
    secret.write_text(MARKER)
    (read_root / "inside.txt").write_text("inside")
    (write_root / "link.txt").symlink_to(secret)
    (write_root / "linkdir").symlink_to(outside)
    (write_root / "hard.txt").hardlink_to(secret)

    # (label, path). Expected to be refused, except where the path only names a file inside.
    escapes = [
        ("absolute", str(secret)),
        ("absolute-new", str(outside / "new.txt")),
        ("dotdot", "../outside/secret.txt"),
        ("root-dotdot", f"{write_root}/../outside/secret.txt"),
        ("file-symlink", str(write_root / "link.txt")),
        ("folder-symlink", str(write_root / "linkdir" / "secret.txt")),
        ("folder-symlink-new", str(write_root / "linkdir" / "new.txt")),
        ("hard-link", str(write_root / "hard.txt")),
        ("sibling-prefix", f"{outside}-sibling/secret.txt"),
        ("write-into-read-root", str(read_root / "new.txt")),
        ("system", "/etc/passwd"),
    ]

    client = Client(args.cli, read_root, write_root, home, args.extra_arg, args.flag_style)
    report = {"cli": Path(args.cli).name, "tools": 0, "path_tools": [], "attempts": 0, "refused": 0, "refused_by_lock": 0, "other_errors": {}, "allowed": [], "escapes": [], "risky_visible": [], "crashes": 0}
    before = snapshot(outside)
    tools = client.start()
    report["tools"] = len(tools)
    report["risky_visible"] = sorted(
        tool["name"] for tool in tools
        if tool["name"] not in hidden and RISKY.search(tool["name"].lower())
    )

    new_document(client, tools)
    calls = []
    for tool in tools:
        name, schema = tool["name"], tool.get("inputSchema") or {}
        if name in hidden:
            continue
        properties = schema.get("properties") or {}
        keys = [key for key in properties if is_path_key(key)]
        if keys:
            report["path_tools"].append(name)
        base = {key: dummy(properties[key]) for key in schema.get("required", []) if key in properties}
        for key in keys:
            calls.append((name, key, properties[key].get("type") == "array", lambda value, base=base, key=key: {**base, key: value}))
    runner, runner_tool, commands = engine_commands(client, tools)
    report["path_commands"] = [command for command, _ in commands]
    if runner:
        id_key = "command" if "command" in runner_tool["inputSchema"].get("properties", {}) else "id"
        for command, keys in commands:
            for key in keys:
                calls.append((f"{runner}:{command}", key, False, lambda value, command=command, key=key, id_key=id_key: {id_key: command, "params": {key: value}}))

    for name, key, as_list, build in calls:
        tool_name = name.split(":")[0]
        if True:
            for label, path in escapes:
                value = [path] if as_list else path
                report["attempts"] += 1
                reply = ""
                is_error = True
                try:
                    message = client.request("tools/call", {"name": tool_name, "arguments": build(value)})
                    reply = json.dumps(message)
                    is_error = "error" in message or bool(message.get("result", {}).get("isError"))
                except (TimeoutError, RuntimeError, BrokenPipeError):
                    report["crashes"] += 1
                    client.stop()
                    client = Client(args.cli, read_root, write_root, home, args.extra_arg, args.flag_style)
                    new_document(client, client.start())
                after = snapshot(outside)
                leaked = MARKER in reply
                changed = after != before or secret.read_text() != MARKER
                read_root_changed = sorted(p.name for p in read_root.iterdir()) != ["inside.txt"]
                system_read = label == "system" and "root:x:0:0" in reply
                if leaked or changed or read_root_changed or system_read:
                    report["escapes"].append({"tool": name, "key": key, "kind": label, "leaked": leaked or system_read, "changed": changed, "read_root_changed": read_root_changed})
                    secret.write_text(MARKER)
                    for extra in outside.iterdir():
                        if extra != secret:
                            extra.unlink() if extra.is_file() else shutil.rmtree(extra)
                    for extra in read_root.iterdir():
                        if extra.name != "inside.txt":
                            extra.unlink() if extra.is_file() else shutil.rmtree(extra)
                    before = snapshot(outside)
                elif is_error:
                    report["refused"] += 1
                    if ROOT_REFUSAL.search(reply):
                        report["refused_by_lock"] += 1
                    else:
                        text = re.sub(r"[\"\\{}]", "", reply)[-110:]
                        report["other_errors"].setdefault(name, text)
                else:
                    report["allowed"].append({"tool": name, "key": key, "kind": label, "reply": reply[-160:]})
    client.stop()
    print(json.dumps(report, indent=1))
    return 1 if report["escapes"] else 0


if __name__ == "__main__":
    sys.exit(main())
