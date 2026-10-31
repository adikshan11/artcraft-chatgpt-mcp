# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Adithya Shankaran

"""Usage counts for /status: web requests by route and connector tool calls, by the hour.

Only fixed route names, tool names, Craft app names, outcomes and timings are kept:
never paths, tool inputs, files, account IDs or IP addresses. Each server process
keeps its current hours in memory and writes them every minute to
``ops/usage/<hour>/<process>.json`` in the file-storage bucket, one record per hour
and process, rewritten in place. Records older than RETENTION_DAYS are deleted.
"""

import asyncio
import json
import logging
import secrets
import statistics
import threading
import time
from datetime import datetime, timedelta, timezone

log = logging.getLogger("bridgecraft.usage")

RETENTION_DAYS = 14
FLUSH_SECONDS = 60
# How long /status reuses a summary before reading the bucket again; reads cost
# against the free tier, writes don't.
SUMMARY_SECONDS = 300
# Timings kept per route or tool each hour, for the percentiles.
MAX_SAMPLES = 1000
PREFIX = "ops/usage/"
PROCESS = secrets.token_hex(6)

# Request paths are reduced to these names, so a file ID or a query never reaches a record.
PAGES = {
    "/": "home",
    "/status": "status",
    "/status.json": "status",
    "/health": "health",
    "/privacy": "legal pages",
    "/terms": "legal pages",
    "/support": "legal pages",
    "/favicon.ico": "icons",
    "/apple-touch-icon.png": "icons",
    "/apple-touch-icon-precomposed.png": "icons",
}
SECTIONS = [
    ("/studio", "studio connector"),
    ("/mcp", "catalog connector"),
    ("/.well-known", "sign-in metadata"),
    ("/static", "static files"),
]

_lock = threading.Lock()
_hours: dict[str, dict] = {}  # hour -> this process's counts
_written: dict[str, str] = {}  # hour -> the JSON last written for it
_first_request = {"pending": True}
# Bucket records of finished hours don't change, so they're read once.
_finished: dict[str, dict] = {}
_summary: dict = {}


def route_name(path: str) -> str:
    if path in PAGES:
        return PAGES[path]
    for prefix, name in SECTIONS:
        if path == prefix or path.startswith(prefix + "/"):
            return name
    return "other"


def hour_of(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H")


def _now_hour() -> dict:
    return _hours.setdefault(hour_of(datetime.now(timezone.utc)), {"requests": {}, "tools": {}, "apps": {}, "cold_starts": 0})


def _sample(samples: list, ms: int):
    if len(samples) < MAX_SAMPLES:
        samples.append(ms)


def record_request(path: str, status: int, ms: int):
    with _lock:
        counts = _now_hour()
        if _first_request["pending"]:
            _first_request["pending"] = False
            counts["cold_starts"] += 1
        route = counts["requests"].setdefault(route_name(path), {"count": 0, "client_errors": 0, "server_errors": 0, "ms": []})
        route["count"] += 1
        route["client_errors"] += 400 <= status < 500
        route["server_errors"] += status >= 500
        _sample(route["ms"], ms)


def record_tool(connector: str, tool: str, app: str | None, ok: bool, ms: int):
    with _lock:
        counts = _now_hour()
        entry = counts["tools"].setdefault(f"{connector}/{tool}", {"calls": 0, "failed": 0, "ms": []})
        entry["calls"] += 1
        entry["failed"] += not ok
        _sample(entry["ms"], ms)
        if app:
            used = counts["apps"].setdefault(app, {"calls": 0, "failed": 0})
            used["calls"] += 1
            used["failed"] += not ok


def timed_tools(server, connector: str, app_of=None):
    """Count every tool call ``server`` handles: its name, outcome and time, and the
    Craft app ``app_of(arguments)`` names, if any. Nothing else about the call is kept."""
    call_tool = server.call_tool
    if getattr(call_tool, "counted", False):
        return

    async def counted(name, arguments, context=None):
        started = time.perf_counter()
        ok = False
        # Looked up first: closing a Craft session forgets which app it was.
        app = app_of(arguments) if app_of and isinstance(arguments, dict) else None
        try:
            result = await call_tool(name, arguments, context)
            ok = not getattr(result, "is_error", False)
            return result
        finally:
            record_tool(connector, name, app, ok, round((time.perf_counter() - started) * 1000))

    counted.counted = True
    server.call_tool = counted


def counted_requests(asgi_app):
    """Wrap an ASGI app so each HTTP request's route, status and time are counted."""

    async def app(scope, receive, send):
        if scope["type"] != "http":
            await asgi_app(scope, receive, send)
            return
        started = time.perf_counter()
        status = {"code": 500}

        async def watch(message):
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            await send(message)

        try:
            await asgi_app(scope, receive, watch)
        finally:
            record_request(scope.get("path", ""), status["code"], round((time.perf_counter() - started) * 1000))

    return app


def flush(store):
    """Write this process's hours that changed since the last write, and forget hours
    that are over. A failed write is retried at the next flush."""
    current = hour_of(datetime.now(timezone.utc))
    with _lock:
        snapshot = {hour: json.dumps(counts, sort_keys=True) for hour, counts in _hours.items()}
    for hour, body in snapshot.items():
        if _written.get(hour) != body:
            store.put_record(f"{PREFIX}{hour}/{PROCESS}.json", body.encode())
            _written[hour] = body
    with _lock:
        for hour in [hour for hour in _hours if hour < current and _written.get(hour) == snapshot.get(hour)]:
            del _hours[hour]
            _written.pop(hour, None)


def prune(store) -> int:
    """Delete usage records older than RETENTION_DAYS; returns how many."""
    oldest = hour_of(datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS))
    deleted = 0
    for key in store.list_records(PREFIX):
        if key[len(PREFIX):].split("/")[0] < oldest:
            store.delete_record(key)
            _finished.pop(key, None)
            deleted += 1
    return deleted


def _records(store, since: str) -> list[tuple[str, dict]]:
    """(hour, counts) for every process's record from ``since`` on, with this
    process's unwritten counts in place of its stored ones."""
    current = hour_of(datetime.now(timezone.utc))
    records = []
    for key in store.list_records(PREFIX) if store else []:
        hour, name = key[len(PREFIX):].split("/")
        if hour < since or (name == f"{PROCESS}.json" and hour in _hours):
            continue
        if key in _finished:
            records.append((hour, _finished[key]))
            continue
        body = store.get_record(key)
        if body is None:
            continue
        counts = json.loads(body)
        # The hour before this one can still get its writers' last flush.
        if hour < hour_of(datetime.now(timezone.utc) - timedelta(hours=1)):
            _finished[key] = counts
        records.append((hour, counts))
    with _lock:
        records.extend((hour, json.loads(json.dumps(counts))) for hour, counts in _hours.items() if hour >= since and hour <= current)
    return records


def percentile(values: list[int], share: float) -> int | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return round(statistics.quantiles(values, n=100, method="inclusive")[round(share * 100) - 1])


def summary(store, hours: int = 24) -> dict:
    """Requests, errors, timings and tool calls over the last ``hours``, one point per hour."""
    now = datetime.now(timezone.utc)
    since = hour_of(now - timedelta(hours=hours - 1))
    timeline = {hour_of(now - timedelta(hours=back)): {"requests": 0, "client_errors": 0, "server_errors": 0, "tool_calls": 0}
                for back in range(hours - 1, -1, -1)}
    routes: dict[str, dict] = {}
    tools: dict[str, dict] = {}
    apps: dict[str, dict] = {}
    cold_starts = 0
    for hour, counts in _records(store, since):
        point = timeline.setdefault(hour, {"requests": 0, "client_errors": 0, "server_errors": 0, "tool_calls": 0})
        cold_starts += counts.get("cold_starts", 0)
        for name, route in counts.get("requests", {}).items():
            total = routes.setdefault(name, {"count": 0, "client_errors": 0, "server_errors": 0, "ms": []})
            for field in ("count", "client_errors", "server_errors"):
                total[field] += route[field]
            total["ms"] += route["ms"]
            point["requests"] += route["count"]
            point["client_errors"] += route["client_errors"]
            point["server_errors"] += route["server_errors"]
        for name, tool in counts.get("tools", {}).items():
            total = tools.setdefault(name, {"calls": 0, "failed": 0, "ms": []})
            total["calls"] += tool["calls"]
            total["failed"] += tool["failed"]
            total["ms"] += tool["ms"]
            point["tool_calls"] += tool["calls"]
        for name, used in counts.get("apps", {}).items():
            total = apps.setdefault(name, {"calls": 0, "failed": 0})
            total["calls"] += used["calls"]
            total["failed"] += used["failed"]

    def timed(entry):
        samples = entry.pop("ms")
        return {**entry, "p50_ms": percentile(samples, 0.5), "p95_ms": percentile(samples, 0.95)}

    return {
        "window_hours": hours,
        "generated_at": now.isoformat(timespec="seconds"),
        "requests": sum(route["count"] for route in routes.values()),
        "client_errors": sum(route["client_errors"] for route in routes.values()),
        "server_errors": sum(route["server_errors"] for route in routes.values()),
        "cold_starts": cold_starts,
        "tool_calls": sum(tool["calls"] for tool in tools.values()),
        "timeline": [{"hour": hour, **point} for hour, point in sorted(timeline.items()) if hour >= since],
        "routes": sorted(({"route": name, **timed(route)} for name, route in routes.items()), key=lambda row: -row["count"]),
        "tools": sorted(({"tool": name, **timed(tool)} for name, tool in tools.items()), key=lambda row: -row["calls"]),
        "apps": sorted(({"app": name, **used} for name, used in apps.items()), key=lambda row: -row["calls"]),
    }


def cached_summary(store, hours: int = 24) -> dict:
    """``summary``, reused for SUMMARY_SECONDS so page views don't each read the bucket."""
    saved = _summary.get(hours)
    if saved and time.monotonic() - saved[0] < SUMMARY_SECONDS:
        return saved[1]
    result = summary(store, hours)
    _summary[hours] = (time.monotonic(), result)
    return result


async def flush_forever(get_store):
    """Write the counts every FLUSH_SECONDS. Without a bucket they stay in memory only."""
    while True:
        await asyncio.sleep(FLUSH_SECONDS)
        await flush_now(get_store)


async def flush_now(get_store):
    try:
        await asyncio.to_thread(flush, get_store())
    except Exception as exc:  # noqa: BLE001 - counting must never take the site down
        log.warning("Usage counts not written: %s", type(exc).__name__)
