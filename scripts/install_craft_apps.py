"""Download the Craft app CLIs listed in craft_apps.json into an image.

Used by the Dockerfile at build time. Each release tarball is checked against
the sha256 pinned in craft_apps.json before anything is extracted, and only the
app's command-line tool (bin/<name>-cli) is kept.

    python scripts/install_craft_apps.py craft_apps.json /opt/craft
"""

import hashlib
import json
import platform
import shutil
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

ARCHES = {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "aarch64", "arm64": "aarch64"}
RELEASE_URL = "https://github.com/storytold/{name}/releases/download/v{version}/{tarball}"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def install(app: dict, arch: str, target: Path, work: Path) -> None:
    name, version = app["name"], app["version"]
    expected = app["sha256"].get(arch)
    if not expected:
        raise SystemExit(f"{name}: no pinned sha256 for linux-{arch}")
    tarball = f"{name}-{version}-linux-{arch}.tar.gz"
    archive = work / tarball
    print(f"==> {name} {version} ({arch})", flush=True)
    with urllib.request.urlopen(RELEASE_URL.format(name=name, version=version, tarball=tarball)) as response:
        with archive.open("wb") as handle:
            shutil.copyfileobj(response, handle)
    actual = sha256_file(archive)
    if actual != expected:
        raise SystemExit(f"{name}: sha256 mismatch for {tarball}\n  expected {expected}\n  got      {actual}")

    member = f"{name}-{version}-linux-{arch}/bin/{name}-cli"
    with tarfile.open(archive) as tar:
        info = tar.getmember(member)
        if not info.isfile():
            raise SystemExit(f"{name}: {member} is not a regular file")
        source = tar.extractfile(info)
        destination = target / name / f"{name}-cli"
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("wb") as handle:
            shutil.copyfileobj(source, handle)
        destination.chmod(0o755)
        # Apache-2.0 requires shipping the license alongside redistributed binaries,
        # and the OFL does the same for the fonts some apps embed.
        doc_dir = f"{name}-{version}-linux-{arch}/share/doc/{name}/"
        for member_info in tar.getmembers():
            license_name = member_info.name.removeprefix(doc_dir)
            if (member_info.isfile() and member_info.name.startswith(doc_dir) and "/" not in license_name
                    and (license_name.startswith(("LICENSE-", "OFL-")))):
                (destination.parent / license_name).write_bytes(tar.extractfile(member_info).read())
    print(f"    installed {destination}", flush=True)


def main() -> None:
    manifest_path, target = Path(sys.argv[1]), Path(sys.argv[2])
    arch = ARCHES.get(platform.machine().lower())
    if arch is None:
        raise SystemExit(f"Unsupported architecture: {platform.machine()}")
    apps = json.loads(manifest_path.read_text())["apps"]
    target.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as work:
        for app in apps:
            install(app, arch, target, Path(work))


if __name__ == "__main__":
    main()
