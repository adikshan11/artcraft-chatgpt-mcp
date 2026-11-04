# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Adithya Shankaran

"""What /status shows: the server's own state, read when the page is asked for.

Uptime, sessions and the last file sweep reset when the server restarts, and the page
says so. The usage counts are the only stored numbers; see usage.py.
"""

import os
import time
from datetime import datetime, timezone

import craft_sessions
import eval_results
import studio_mcp
import usage
from media_storage import S3Storage, StorageError

# The Craft apps ArtCraft lists that Studio does not host yet, why, and the open
# upstream pull requests that would let me host them.
_NO_LOCK = "Its command-line build can't be locked to one user's folder yet. The open pull requests below add that lock."
NOT_HOSTED_APPS = [
    {"name": "effectcraft", "title": "EffectCraft", "short": "Merged, not in 0.19.0",
     "reason": "storytold/effectcraft#2719 is merged. The 0.19.0 download can still show footage from outside the folder, so Studio does not host it yet.",
     "prs": [2719]},
    {"name": "filmcraft", "title": "FilmCraft", "short": "Folder lock in review", "reason": _NO_LOCK, "prs": [1657]},
    {"name": "cadcraft", "title": "CADCraft", "short": "Folder lock in review", "reason": _NO_LOCK, "prs": [1904]},
    {"name": "gridcraft", "title": "GridCraft", "short": "Folder lock in review", "reason": _NO_LOCK,
     "prs": [480, 2739, 2857]},
    {"name": "lightcraft", "title": "LightCraft", "short": "Folder lock in review", "reason": _NO_LOCK, "prs": [2176]},
]
for _app in NOT_HOSTED_APPS:
    _app["pr_links"] = [{"number": n, "url": f"https://github.com/storytold/{_app['name']}/pull/{n}"} for n in _app["prs"]]


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
    elif not sweep["ok"]:
        rows.append(_row("File cleanup", "error", f"Last run failed at {sweep['at']:%H:%M} UTC; it retries hourly"))
    else:
        rows.append(_row("File cleanup", "ok",
                         f"Last ran at {sweep['at']:%H:%M} UTC and deleted {sweep['deleted']} expired "
                         f"file{'' if sweep['deleted'] == 1 else 's'}"))
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
    service_rows, app_rows = services(), apps()
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
        "evals": eval_results.latest(),
        "on_render": bool(os.environ.get("RENDER")),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
