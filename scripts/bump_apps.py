# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Adithya Shankaran

"""Move craft_apps.json and the files that repeat its versions to the newest releases.

Used by the release-watch workflow, which then builds the image, runs the escape checks and
evals on what changed and opens a pull request. It only prints what it did. A release with
no checksum for a Linux tarball is reported and left alone.

    python scripts/bump_apps.py > bumps.json
"""

import json
import os
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import release_watch  # noqa: E402

RELEASE_URL = "https://api.github.com/repos/storytold/{name}/releases/tags/v{version}"
CONSTANTS = {"photocraft": ("craft_app_mcp.py", "PHOTOCRAFT_VERSION"), "pdfcraft": ("pdfcraft_mcp.py", "PDFCRAFT_VERSION")}
ARCHES = ("x86_64", "aarch64")


def asset_digests(name: str, version: str) -> dict:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "BridgeCraft/1.0"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    req = urllib.request.Request(RELEASE_URL.format(name=name, version=version), headers=headers)
    with urllib.request.urlopen(req, timeout=30) as response:
        assets = json.load(response)["assets"]
    return {asset["name"]: asset["digest"].split(":", 1)[1] for asset in assets if asset.get("digest", "").startswith("sha256:")}


def bump_manifest(text: str, app: dict, version: str, digests: dict) -> str:
    """Replace one app's version and checksums in place, so the file's layout and other entries stay."""
    old = app["version"]
    start = text.index(f'"name": "{app["name"]}"')
    end = text.find('"name": "', start + 10)
    end = len(text) if end < 0 else end
    block = text[start:end].replace(f'"version": "{old}"', f'"version": "{version}"')
    for arch in ARCHES:
        block = block.replace(app["sha256"][arch], digests[f"{app['name']}-{version}-linux-{arch}.tar.gz"])
    block = re.sub(r'"confinement_checked": "[^"]*"', '"confinement_checked": "pending the release-watch run"', block)
    return text[:start] + block + text[end:]


def bump_readme(text: str, app: dict, version: str, digests: dict) -> str:
    """The README's app table, plus the local-install checksums for the two apps it documents."""
    name, title, old = app["name"], app["title"], app["version"]
    for line in re.findall(rf"^\| {title} {re.escape(old)} \|.*$", text, flags=re.M):
        asset = re.search(r"`([^`]+\.(?:zip|tar\.gz))`", line)
        checksum = re.search(r"`([0-9a-f]{64})`", line)
        if asset and checksum:
            text = text.replace(checksum.group(1), digests[asset.group(1).replace(old, version)])
    for before, after in ((f"{title} {old}", f"{title} {version}"), (f"{name}-{old}", f"{name}-{version}"),
                          (f"{name}-cli-{old}", f"{name}-cli-{version}"), (f"app={name} v={old}", f"app={name} v={version}"),
                          (f'$app = "{name}"; $v = "{old}"', f'$app = "{name}"; $v = "{version}"')):
        text = text.replace(before, after)
    return text


def main() -> int:
    manifest_path, readme_path = ROOT / "craft_apps.json", ROOT / "README.md"
    manifest = manifest_path.read_text()
    readme = readme_path.read_text()
    apps = json.loads(manifest)["apps"]
    latest = release_watch.fetch_latest([app["name"] for app in apps])
    report = {"bumped": [], "skipped": []}
    for app in apps:
        name, old = app["name"], app["version"]
        release = latest.get(name)
        if not release or release_watch._key(release["version"]) <= release_watch._key(old):
            continue
        version = release["version"]
        digests = asset_digests(name, version)
        missing = [arch for arch in ARCHES if f"{name}-{version}-linux-{arch}.tar.gz" not in digests]
        if missing:
            report["skipped"].append({"app": name, "version": version, "why": f"no checksum for linux-{missing[0]}"})
            continue
        manifest = bump_manifest(manifest, app, version, digests)
        readme = bump_readme(readme, app, version, digests)
        if name in CONSTANTS:
            path, constant = CONSTANTS[name]
            source = (ROOT / path).read_text()
            (ROOT / path).write_text(source.replace(f'{constant} = "{old}"', f'{constant} = "{version}"'))
        report["bumped"].append({"app": name, "from": old, "to": version,
                                 "notes": f"https://github.com/storytold/{name}/releases/tag/v{version}"})
    manifest_path.write_text(manifest)
    readme_path.write_text(readme)
    json.dump(report, sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
