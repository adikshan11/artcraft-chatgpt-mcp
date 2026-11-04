# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Adithya Shankaran

"""The latest report from the weekly evals, for /status.

The workflow in .github/workflows/evals.yml publishes it to the repository's
eval-results branch. Only local copies with EVAL_RESULTS_URL set, and the hosted site,
fetch it. Only the fields /status uses are kept.
"""

import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib import request

log = logging.getLogger("bridgecraft.evals")

RESULTS_URL = "https://raw.githubusercontent.com/adikshan11/bridgecraft/eval-results/latest.json"
SCHEMA = 1
CACHE_SECONDS = 3600
WAIT_SECONDS = 2
MAX_BYTES = 256 * 1024
# A weekly run, so past this one has been missed.
STALE_AFTER = timedelta(days=9)

_cache: dict = {}
_thread: dict = {}
_lock = threading.Lock()


def _results_url() -> str | None:
    return os.environ.get("EVAL_RESULTS_URL") or (RESULTS_URL if os.environ.get("RENDER") else None)


def _short(value, limit=300) -> str:
    return str(value)[:limit]


def parse(report: dict, now: datetime | None = None) -> dict | None:
    """What /status shows from a report, or None if it isn't one this code can read."""
    try:
        if report["schema"] != SCHEMA:
            return None
        generated_at = datetime.fromisoformat(report["generated_at"])
        apps = []
        for app in report["apps"]:
            checks = app["checks"]
            apps.append({
                "app": _short(app["app"], 40),
                "title": _short(app["title"], 40),
                "version": _short(app["version"], 40),
                "passed": app["passed"] is True,
                "ms": int(app["ms"]),
                "checks": len(checks),
                "checks_passed": sum(check["passed"] is True for check in checks),
                "failures": [{"name": _short(check["name"], 120), "detail": _short(check.get("detail", ""))}
                             for check in checks if check["passed"] is not True],
            })
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    if not apps:
        return None
    age = (now or datetime.now(timezone.utc)) - generated_at
    return {
        "passed": report["passed"] is True and all(app["passed"] for app in apps),
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "date": f"{generated_at:%-d %B %Y}",
        "commit": _short(report.get("commit", ""), 7),
        "stale": age > STALE_AFTER,
        "apps": apps,
        "apps_passed": sum(app["passed"] for app in apps),
    }


def _fetch(url: str):
    saved = _cache.get("saved")
    try:
        req = request.Request(url, headers={"User-Agent": "BridgeCraft/1.0"})
        with request.urlopen(req, timeout=10) as response:
            result = parse(json.loads(response.read(MAX_BYTES + 1)[:MAX_BYTES]))
        if result is None:
            log.warning("The eval report couldn't be read.")
    except (OSError, ValueError) as exc:
        log.warning("Eval report not fetched: %s", type(exc).__name__)
        result = None
    # Keep showing the last good report if this fetch failed.
    _cache["saved"] = (time.monotonic(), result or (saved[1] if saved else None))


def latest() -> dict | None:
    """The latest report, reused for CACHE_SECONDS. A slow fetch can't hold up the page."""
    url = _results_url()
    if not url:
        return None
    saved = _cache.get("saved")
    if saved and time.monotonic() - saved[0] < CACHE_SECONDS:
        return saved[1]
    with _lock:
        thread = _thread.get("running")
        if not thread or not thread.is_alive():
            thread = threading.Thread(target=_fetch, args=(url,), daemon=True)
            _thread["running"] = thread
            thread.start()
    thread.join(WAIT_SECONDS)
    saved = _cache.get("saved")
    return saved[1] if saved else None
