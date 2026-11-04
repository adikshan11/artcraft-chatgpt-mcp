# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Adithya Shankaran

"""Write the release-watch pull request, and record each checked app's sweep in the manifest.

    python scripts/release_watch_pr.py bumps.json escapes.json evals.json > body.md

ESCAPES, EVALS and TESTS hold the outcome of each step in the workflow.
"""

import json
import os
import sys
from datetime import date
from pathlib import Path

MANIFEST = Path(__file__).resolve().parent.parent / "craft_apps.json"


def _load(path: str) -> dict:
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}


def record_sweep(text: str, name: str, version: str, sweep: dict) -> str:
    """Replace the placeholder bump_apps.py left, in that app's entry only."""
    start = text.index(f'"name": "{name}"')
    line = (f"{date.today()}, {version} linux-x86_64, scripts/escape_checks.py: {sweep['attempts']} attempts through "
            f"{len(sweep['path_tools'])} tools and {len(sweep['path_commands'])} engine commands, "
            f"{len(sweep['escapes'])} reached outside the roots")
    return text[:start] + text[start:].replace("pending the release-watch run", line, 1)


def main() -> int:
    bumps, escapes, evals = (_load(path) for path in sys.argv[1:4])
    manifest = MANIFEST.read_text()
    lines = ["New Craft releases, found by the release-watch workflow.", "",
             "| App | From | To | Attempts to escape | Got out | Evals |", "| --- | --- | --- | --- | --- | --- |"]
    results = {row["app"]: row for row in evals.get("apps", [])}
    for row in bumps["bumped"]:
        sweep = escapes.get(row["app"], {})
        result = results.get(row["app"])
        if "attempts" in sweep:
            manifest = record_sweep(manifest, row["app"], row["to"], sweep)
        lines.append(f"| [{row['app']}]({row['notes']}) | {row['from']} | {row['to']} | {sweep.get('attempts', 'failed')} "
                     f"| {len(sweep.get('escapes', [])) if 'attempts' in sweep else 'failed'} "
                     f"| {'passed' if result and result['passed'] else 'failed'} |")
    MANIFEST.write_text(manifest)
    lines += ["", "| Step | Outcome |", "| --- | --- |"]
    for label, env in (("Escape checks", "ESCAPES"), ("Evals", "EVALS"), ("Unit tests", "TESTS")):
        lines.append(f"| {label} | {os.environ.get(env, 'not run')} |")
    for row in bumps.get("skipped", []):
        lines += ["", f"Left {row['app']} {row['version']} alone: {row['why']}."]
    lines += ["", "Read the release notes for new commands that reach the network, run code or take a path, and add "
                  "them to `hidden_tools` or `blocked_commands` before merging."]
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
