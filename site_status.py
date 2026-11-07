# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Adithya Shankaran

"""What /status shows: the server's own state, read when the page is asked for.

Uptime, sessions and the last file sweep reset when the server restarts, and the page
says so. The usage counts are the only stored numbers; see usage.py.
"""

import os
import time
from datetime import datetime, timedelta, timezone

import craft_sessions
import eval_results
import release_watch
import studio_mcp
import usage
from media_storage import S3Storage, StorageError

SWEEP_LATE_AFTER = timedelta(hours=2)

# The Craft apps ArtCraft lists that Studio does not host yet, why, and the open
# upstream pull requests or issues that would let me host them.
_NO_LOCK = "Its command-line build can't be locked to one user's folder yet. The open pull requests below add that lock."
NOT_HOSTED_APPS = [
    {"name": "filmcraft", "title": "FilmCraft", "short": "Folder lock in review", "reason": _NO_LOCK, "prs": [1657]},
    {"name": "cadcraft", "title": "CADCraft", "short": "Folder lock in review", "reason": _NO_LOCK, "prs": [1904]},
    {"name": "gridcraft", "title": "GridCraft", "short": "Folder lock in review", "reason": _NO_LOCK,
     "prs": [480, 2739, 2857]},
    {"name": "lightcraft", "title": "LightCraft", "short": "Folder lock in review", "reason": _NO_LOCK, "prs": [2176]},
    {"name": "craft-anything", "title": "Craft Anything", "short": "No folder lock yet",
     "reason": "It runs commands, installs tools and opens websites for one person on their own computer, and it can't "
               "be locked to one user's folder yet. The issue below asks for that.",
     "prs": [], "issues": [833]},
]
for _app in NOT_HOSTED_APPS:
    _base = f"https://github.com/storytold/{_app['name']}"
    _app["pr_links"] = [{"number": n, "kind": "pull request", "url": f"{_base}/pull/{n}"} for n in _app["prs"]] + [
        {"number": n, "kind": "issue", "url": f"{_base}/issues/{n}"} for n in _app.get("issues", [])]


# Other public tools and projects. Kept separate from the official app list above:
# AniCraft is an ArtCraft-team source project with no published release or checked folder lock.
# Launcher installs local apps; SolidCraft is an independent project, not an official Storytold app.
RELATED_PROJECTS = [
    {
        "title": "AniCraft",
        "status": "ArtCraft team project · MCP source, but no published release or verified folder lock",
        "repo_url": "https://github.com/storytold/AniCraft",
        "releases_url": "https://github.com/storytold/AniCraft/releases",
    },
    {
        "title": "ArtCraft Launcher",
        "status": "Installs and updates Craft apps locally · latest stable v0.16.0",
        "repo_url": "https://github.com/storytold/craft-launcher",
        "releases_url": "https://github.com/storytold/craft-launcher/releases/latest",
    },
    {
        "title": "SolidCraft",
        "status": "Independent CAD project by Tangency Labs · stable v0.30.1; v0.40.0-rc.2 is prerelease; folder lock not verified",
        "repo_url": "https://github.com/Tangency-Labs/SolidCraft",
        "releases_url": "https://github.com/Tangency-Labs/SolidCraft/releases",
    },
]


def _duration(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 1:
        return "under a minute"
    if minutes < 60:
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    if hours < 48:
        return f"{hours} h {minutes} min"
    return f"{hours // 24} days"


def _row(name, health, text):
    return {"name": name, "health": health, "text": text}


def services() -> list[dict]:
    on_render = bool(os.environ.get("RENDER"))
    rows = [
        _row("Website", "ok", "Serving this page"),
        _row("Project catalog connector", "ok", "Read-only Storytold project search at /mcp/"),
        _row("Studio connector", "ok", "File tools and Craft apps at /studio"),
    ]
    if os.environ.get("OAUTH_ISSUER"):
        rows.append(_row("Sign-in", "ok", "OAuth is set up; every Studio call needs a signed-in user"))
    else:
        rows.append(_row("Sign-in", "warn" if on_render else "off",
                         "Not set up, so Studio refuses every call" if on_render else "Off on this local copy"))
    try:
        store = studio_mcp.storage()
    except StorageError:
        store = None
    if store is None:
        rows.append(_row("File storage", "error", "Not set up, so files can't be saved"))
    elif isinstance(store, S3Storage):
        rows.append(_row("File storage", "ok", "S3-compatible bucket, one private folder per user"))
    else:
        rows.append(_row("File storage", "ok", "Local folder on this copy"))
    sweep = studio_mcp.last_sweep
    if not sweep:
        rows.append(_row("File cleanup", "off", "Hasn't run since the server started"))
    elif datetime.now(timezone.utc) - sweep["at"] > SWEEP_LATE_AFTER:
        rows.append(_row("File cleanup", "error", f"Hasn't run since {sweep['at']:%H:%M} UTC; it should run hourly"))
    elif not sweep["ok"]:
        rows.append(_row("File cleanup", "error", f"Last run failed at {sweep['at']:%H:%M} UTC; it retries hourly"))
    else:
        rows.append(_row("File cleanup", "ok",
                         f"Last ran at {sweep['at']:%H:%M} UTC and deleted {sweep['deleted']} expired "
                         f"file{'' if sweep['deleted'] == 1 else 's'}"))
    if craft_sessions.network_is_cut():
        rows.append(_row("Session network", "ok", "Craft sessions run with no network"))
    else:
        rows.append(_row("Session network", "off", "This host doesn't let Craft sessions give up their network"))
    return rows


def apps() -> list[dict]:
    rows = []
    for app in craft_sessions.load_apps().values():
        if app.installed:
            rows.append({**_row(app.title, "ok", f"Version {app.version}, checksum checked when the server was built"),
                         "app": app.name})
        elif os.environ.get("RENDER"):
            rows.append({**_row(app.title, "error", f"Version {app.version} is listed but missing from this server"),
                         "app": app.name})
        else:
            rows.append({**_row(app.title, "off", f"Version {app.version}, not installed on this local copy"),
                         "app": app.name})
    for app in NOT_HOSTED_APPS:
        rows.append({**_row(app["title"], "off", f"Not hosted. {app['reason']}"), "app": app["name"],
                     "prs": app["pr_links"]})
    return rows


def _ago(moment: datetime) -> str:
    return _duration((datetime.now(timezone.utc) - moment).total_seconds()) + " ago"


def freshness(evals: dict | None) -> tuple[list[dict], dict | None]:
    """Whether the numbers on this page, and the apps behind them, are up to date."""
    pins = {app.name: app.version for app in craft_sessions.load_apps().values()}
    rows, versions = [], None
    feed = release_watch.latest(pins)
    if feed is None:
        on_render = bool(os.environ.get("RENDER"))
        rows.append(_row("Release list", "warn" if on_render else "off",
                         "Can't read Storytold's release list yet" if on_render else "Not read on this local copy"))
    else:
        versions = release_watch.compare(pins, feed["latest"])
        stale = release_watch.feed_stale(feed["fetched_at"])
        rows.append(_row("Release list", "warn" if stale else "ok", f"Read {_ago(feed['fetched_at'])}"))
        behind = versions["behind"]
        total = len(pins)
        if behind:
            names = ", ".join(f"{row['app']} {row['pinned']} to {row['latest']}" for row in behind)
            rows.append(_row("App versions", "warn" if versions["overdue"] else "ok",
                             f"{versions['current']} of {total} on the latest release. New: {names}"))
        elif versions["unknown"]:
            rows.append(_row("App versions", "warn", f"No answer for {', '.join(versions['unknown'])}"))
        else:
            rows.append(_row("App versions", "ok", f"All {total} on the latest release"))
    if evals:
        tested = {app["app"]: app["version"] for app in evals["apps"]}
        moved = sorted(name for name, version in pins.items() if tested.get(name) != version)
        if evals["stale"] or moved:
            why = f"; {', '.join(moved)} changed since" if moved else ""
            rows.append(_row("App checks", "warn", f"Last ran on {evals['date']}{why}"))
        else:
            rows.append(_row("App checks", "ok", f"Ran on {evals['date']} against the versions in use"))
    return rows, versions


def _size(size: int) -> str:
    for unit, scale in (("GB", 1000**3), ("MB", 1000**2), ("KB", 1000)):
        if size >= scale:
            return f"{size / scale:.1f}".removesuffix(".0") + f" {unit}"
    return f"{size} bytes"


def _chart(timeline: list[dict], field: str, unit: str) -> dict:
    values = [point[field] for point in timeline]
    highest = max(values, default=0)
    return {
        "total": sum(values),
        "unit": unit,
        "highest": highest,
        "bars": [{"hour": point["hour"][11:] + ":00", "value": point[field],
                  "height": round(point[field] / highest * 100, 1) if highest else 0}
                 for point in timeline],
    }


def usage_counts() -> dict | None:
    try:
        store = studio_mcp.storage()
    except StorageError:
        store = None
    return usage.cached_summary(store)


def usage_view(counts: dict | None) -> dict | None:
    """The usage counts as /status draws them."""
    if not counts:
        return None
    craft = craft_sessions.load_apps()
    view = {
        "requests": _chart(counts["timeline"], "requests", "requests"),
        "tool_calls": _chart(counts["timeline"], "tool_calls", "tool calls"),
        "apps": [{**row, "title": craft[row["app"]].title if row["app"] in craft else row["app"]} for row in counts["apps"]],
        "storage": None,
    }
    used = counts.get("storage")
    if used:
        free = used["free_bytes"]
        view["storage"] = {
            "used": _size(used["bytes"]),
            "files": used["files"],
            "old_versions": used["old_versions"],
            "free": _size(free) if free else None,
            "percent": min(100, round(used["bytes"] / free * 100, 2)) if free else None,
        }
    return view


def summary(started_at: float) -> dict:
    evals = eval_results.latest()
    fresh_rows, versions = freshness(evals)
    service_rows, app_rows = services() + fresh_rows, apps()
    healths = {row["health"] for row in service_rows + app_rows}
    overall = "error" if "error" in healths else "warn" if "warn" in healths else "ok"
    open_sessions = len(craft_sessions.manager.sessions)
    commit = os.environ.get("RENDER_GIT_COMMIT", "")[:7]
    return {
        "overall": overall,
        "headline": {"ok": "Everything is working", "warn": "Working, with something to know",
                     "error": "Something is broken"}[overall],
        "services": service_rows,
        "apps": app_rows,
        "facts": {
            "version": commit or "local copy",
            "up_for": _duration(time.time() - started_at),
            "open_sessions": open_sessions,
            "max_sessions": craft_sessions.MAX_SESSIONS,
            "max_sessions_per_user": craft_sessions.MAX_SESSIONS_PER_USER,
            "idle_minutes": craft_sessions.IDLE_SECONDS // 60,
            "retention_days": studio_mcp.RETENTION_DAYS,
        },
        "usage": usage_counts(),
        "evals": evals,
        "versions": versions,
        "on_render": bool(os.environ.get("RENDER")),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
