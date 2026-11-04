# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Adithya Shankaran

"""Which Craft releases exist, compared with the ones this server runs.

Storytold publishes no release events, so this reads the list its own launcher reads, and
asks GitHub about any hosted app that list leaves out. A failed fetch keeps the last good
answer and waits RETRY_SECONDS before trying again, so an outage upstream can't slow /status
or turn into a stream of requests.
"""

import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib import request

log = logging.getLogger("bridgecraft.release_watch")

RELEASE_INFO_URL = "https://api.storyteller.ai/v1/craft_apps/release_info"
GITHUB_LATEST_URL = "https://api.github.com/repos/storytold/{name}/releases/latest"
GITHUB_RELEASE_PAGE = "https://github.com/storytold/{name}/releases/latest"
CACHE_SECONDS = 3600
RETRY_SECONDS = 900
WAIT_SECONDS = 2
TIMEOUT_SECONDS = 10
MAX_BYTES = 8 * 1024 * 1024
# How long the feed can go unread, and a new release can wait for me, before /status says so.
FEED_STALE_AFTER = timedelta(hours=6)
BEHIND_GRACE = timedelta(days=7)
_VERSION = re.compile(r"\d+(\.\d+)*")

_cache: dict = {}
_thread: dict = {}
_lock = threading.Lock()


def _key(version: str) -> tuple:
    return tuple(int(part) for part in version.split("."))


def _get_json(url: str, headers: dict | None = None) -> dict:
    req = request.Request(url, headers={"User-Agent": "BridgeCraft/1.0", **(headers or {})})
    with request.urlopen(req, timeout=TIMEOUT_SECONDS) as response:
        return json.loads(response.read(MAX_BYTES + 1)[:MAX_BYTES])


def parse_release_info(data: dict) -> dict:
    """{app: {version, published_at}} from Storytold's list, keeping only well-formed entries."""
    latest = {}
    for name, entry in data["release_info"]["apps"].items():
        release = (entry or {}).get("latest_release") or {}
        version = release.get("version")
        if isinstance(version, str) and _VERSION.fullmatch(version):
            latest[name] = {"version": version, "published_at": release.get("published_at")}
    return latest


def fetch_latest(names) -> dict:
    """The newest release of each named app. Raises only if Storytold's list can't be read."""
    latest = parse_release_info(_get_json(RELEASE_INFO_URL))
    headers = {"Accept": "application/vnd.github+json"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    found = {}
    for name in names:
        if name in latest:
            found[name] = latest[name]
            continue
        release = _from_api(name, headers) or _from_release_page(name)
        if release:
            found[name] = release
        else:
            log.warning("Latest %s release not fetched.", name)
    return found


def _from_api(name: str, headers: dict) -> dict | None:
    try:
        release = _get_json(GITHUB_LATEST_URL.format(name=name), headers)
        version = str(release["tag_name"]).removeprefix("v")
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return {"version": version, "published_at": release.get("published_at")} if _VERSION.fullmatch(version) else None


def _from_release_page(name: str) -> dict | None:
    """GitHub's API allows 60 calls an hour per address, and Render's addresses are shared. The
    public page isn't counted that way: it redirects to the newest tag, but gives no date."""
    req = request.Request(GITHUB_RELEASE_PAGE.format(name=name), method="HEAD", headers={"User-Agent": "BridgeCraft/1.0"})
    try:
        with request.urlopen(req, timeout=TIMEOUT_SECONDS) as response:
            tag = response.geturl().rsplit("/tag/", 1)[1]
    except (OSError, ValueError, IndexError):
        return None
    version = tag.removeprefix("v")
    return {"version": version, "published_at": None} if _VERSION.fullmatch(version) else None


def compare(pins: dict, latest: dict, now: datetime | None = None) -> dict:
    """Each pinned app against its newest release. An app with no answer counts as unknown."""
    now = now or datetime.now(timezone.utc)
    rows, overdue = [], False
    for name, pinned in pins.items():
        release = latest.get(name)
        if not release:
            rows.append({"app": name, "pinned": pinned, "latest": None, "state": "unknown"})
            continue
        behind = _key(release["version"]) > _key(pinned)
        rows.append({"app": name, "pinned": pinned, "latest": release["version"],
                     "published_at": release.get("published_at"),
                     "state": "behind" if behind else "current"})
        if behind and release.get("published_at"):
            try:
                published = datetime.fromisoformat(release["published_at"].replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                continue
            overdue = overdue or now - published > BEHIND_GRACE
    return {"apps": rows, "overdue": overdue,
            "current": sum(row["state"] == "current" for row in rows),
            "behind": [row for row in rows if row["state"] == "behind"],
            "unknown": [row["app"] for row in rows if row["state"] == "unknown"]}


def _watching() -> bool:
    return bool(os.environ.get("RELEASE_WATCH") or os.environ.get("RENDER"))


def _fetch(names) -> None:
    try:
        # An app with no answer this time keeps its last one.
        _cache["latest"] = {**_cache.get("latest", {}), **fetch_latest(names)}
        _cache["fetched_at"] = datetime.now(timezone.utc)
        _cache["next_at"] = time.monotonic() + CACHE_SECONDS
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        log.warning("Release list not fetched: %s", type(exc).__name__)
        _cache["next_at"] = time.monotonic() + RETRY_SECONDS


def latest(names) -> dict | None:
    """{latest, fetched_at}, or None if nothing has been read yet. A slow fetch can't hold up a page."""
    if not _watching():
        return None
    if time.monotonic() >= _cache.get("next_at", 0):
        with _lock:
            thread = _thread.get("running")
            if not thread or not thread.is_alive():
                thread = threading.Thread(target=_fetch, args=(tuple(names),), daemon=True)
                _thread["running"] = thread
                thread.start()
        thread.join(WAIT_SECONDS)
    if "latest" not in _cache:
        return None
    return {"latest": _cache["latest"], "fetched_at": _cache["fetched_at"]}


def feed_stale(fetched_at: datetime, now: datetime | None = None) -> bool:
    return (now or datetime.now(timezone.utc)) - fetched_at > FEED_STALE_AFTER
