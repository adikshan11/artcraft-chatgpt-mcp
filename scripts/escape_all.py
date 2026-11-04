# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Adithya Shankaran

"""Run scripts/escape_checks.py over the apps in craft_apps.json, as Studio starts them.

    python scripts/escape_all.py craft_apps.json /opt/craft [app ...] > escapes.json

Exits 1 if any attempt got out or an app couldn't be checked.
"""

import json
import subprocess
import sys
from pathlib import Path

CHECKS = Path(__file__).with_name("escape_checks.py")
TIMEOUT_SECONDS = 1500


def main() -> int:
    manifest, apps_dir, *only = sys.argv[1:]
    report = {}
    for app in json.loads(Path(manifest).read_text())["apps"]:
        name = app["name"]
        if only and name not in only:
            continue
        command = [sys.executable, str(CHECKS), f"{apps_dir}/{name}/{name}-cli",
                   f"--hidden={','.join(app.get('hidden_tools', []))}",
                   f"--blocked={','.join(app.get('blocked_commands', []))}",
                   *[f"--extra-arg={arg}" for arg in app.get("mcp_args", [])]]
        if app.get("root_flags"):
            command += ["--flag-style", "plain"]
        try:
            done = subprocess.run(command, capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
            report[name] = json.loads(done.stdout)
        except (subprocess.TimeoutExpired, ValueError) as exc:
            report[name] = {"failed": type(exc).__name__}
        print(name, {key: report[name].get(key) for key in ("attempts", "refused_by_lock", "crashes", "failed")},
              len(report[name].get("escapes", [])), "escapes", file=sys.stderr, flush=True)
    json.dump(report, sys.stdout, indent=1)
    print()
    return 1 if any(row.get("failed") or row.get("escapes") for row in report.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
