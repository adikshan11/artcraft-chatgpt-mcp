# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Adithya Shankaran

"""Protection against overload: a per-client request limit, load shedding, and a circuit breaker.

The limiter and the in-flight cap guard the whole ASGI app. The breaker guards calls to services
this server depends on (ArtCraft and the file storage), so a dead upstream fails fast instead of
holding a request open for its full timeout.
"""

import json
import math
import os
import threading
import time

# Paths that hold a connection open or must always answer: they are never shed for load.
HEALTH_PATH = "/health"
LONG_LIVED_PREFIXES = ("/mcp", "/studio", "/.well-known/oauth-protected-resource")
MAX_TRACKED_CLIENTS = 10_000


class CircuitOpen(Exception):
    """Raised instead of calling a service that has been failing; ``retry_after`` is in seconds."""

    def __init__(self, name, retry_after):
        super().__init__(f"{name} is not responding")
        self.name = name
        self.retry_after = max(1, math.ceil(retry_after))


class CircuitBreaker:
    """Stops calling a failing service for a while.

    After ``threshold`` failures in a row the circuit opens and every call fails at once. After
    ``cooldown`` seconds one call, the probe, is let through; if it succeeds the circuit closes,
    and if it fails the circuit stays open for another cooldown. A probe that never reports back
    is given up on after another cooldown.

    ``before()`` returns a ticket. Pass it to ``success()`` or ``failure()``: a call admitted
    before the circuit opened or closed can finish late, and its result must not release the
    probe or reset the count, so results from an older ticket are ignored.
    """

    def __init__(self, name, threshold=5, cooldown=30.0, clock=time.monotonic):
        self.name = name
        self.threshold = threshold
        self.cooldown = cooldown
        self._clock = clock
        self._lock = threading.Lock()
        self._failures = 0
        self._opened_at = None
        self._probe_since = None
        self._epoch = 0

    def before(self):
        """Raise CircuitOpen when the call should not be made; otherwise return its ticket."""
        with self._lock:
            if self._opened_at is None:
                return self._epoch
            now = self._clock()
            waited = now - self._opened_at
            if waited < self.cooldown:
                raise CircuitOpen(self.name, self.cooldown - waited)
            if self._probe_since is not None and now - self._probe_since < self.cooldown:
                raise CircuitOpen(self.name, 1)
            self._probe_since = now
            return self._epoch

    def success(self, ticket=None):
        """Record a call that reached the service, whatever the service answered to the request."""
        with self._lock:
            if ticket is not None and ticket != self._epoch:
                return
            if self._opened_at is not None:
                self._epoch += 1
            self._failures = 0
            self._opened_at = None
            self._probe_since = None

    def failure(self, ticket=None):
        """Record a call that failed because the service is unreachable or erroring."""
        with self._lock:
            if ticket is not None and ticket != self._epoch:
                return
            self._failures += 1
            if self._failures >= self.threshold or self._opened_at is not None:
                if self._opened_at is not None or self._failures == self.threshold:
                    self._epoch += 1
                self._opened_at = self._clock()
                self._probe_since = None

    @property
    def is_open(self):
        """True while calls are being refused."""
        with self._lock:
            return self._opened_at is not None


class RateLimiter:
    """Counts requests per client in fixed windows.

    At most MAX_TRACKED_CLIENTS clients are tracked. When the table is full, clients it has not
    seen share one overflow count with a larger allowance, so a flood of new addresses is still
    limited and the table cannot grow.
    """

    OVERFLOW = "\0overflow"
    OVERFLOW_FACTOR = 20

    def __init__(self, limit, window, clock=time.monotonic):
        self.limit = limit
        self.window = window
        self._clock = clock
        self._lock = threading.Lock()
        self._seen = {}

    def check(self, key):
        """Return 0 when the request is allowed, or the seconds to wait before trying again."""
        now = self._clock()
        with self._lock:
            if key not in self._seen and len(self._seen) >= MAX_TRACKED_CLIENTS:
                self._seen = {k: v for k, v in self._seen.items() if now - v[0] < self.window}
                if len(self._seen) >= MAX_TRACKED_CLIENTS:
                    key = self.OVERFLOW
            allowance = self.limit * (self.OVERFLOW_FACTOR if key == self.OVERFLOW else 1)
            started, count = self._seen.get(key, (now, 0))
            if now - started >= self.window:
                started, count = now, 0
            if count >= allowance:
                return max(1, math.ceil(self.window - (now - started)))
            self._seen[key] = (started, count + 1)
            return 0


def client_key(scope, header_name=None):
    """The address a request is counted under.

    ``header_name`` names a header that the hosting proxy sets and a client cannot forge, such as
    CF-Connecting-IP behind Render. Left unset, the socket's address is used, which behind a proxy
    is the proxy.
    """
    if header_name:
        wanted = header_name.lower().encode()
        for name, value in scope.get("headers", []):
            if name == wanted and value:
                return value.decode("latin-1").split(",")[0].strip()
    client = scope.get("client")
    return client[0] if client else "unknown"


def _env_number(name, default):
    try:
        return max(1, int(os.environ.get(name, default)))
    except ValueError:
        return default


async def _refuse(send, status, retry_after, message):
    body = json.dumps({"error": message}).encode()
    await send({"type": "http.response.start", "status": status, "headers": [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
        (b"retry-after", str(retry_after).encode()),
        (b"cache-control", b"no-store"),
    ]})
    await send({"type": "http.response.body", "body": body})


def load_guard(inner, *, limit=None, window=None, max_inflight=None, header_name=None):
    """Wrap an ASGI app with a per-client request limit and a cap on requests in flight.

    A client over its limit gets 429, and a server already running ``max_inflight`` ordinary
    requests answers 503, both with Retry-After. The health check is never limited, and the
    connector paths, whose requests can stay open for a long time, are limited but not shed.
    """
    limiter = RateLimiter(
        limit or _env_number("RATE_LIMIT_PER_CLIENT", 120),
        window or _env_number("RATE_LIMIT_WINDOW_SECONDS", 10),
    )
    cap = max_inflight or _env_number("MAX_INFLIGHT_REQUESTS", 64)
    header_name = header_name if header_name is not None else os.environ.get("CLIENT_IP_HEADER", "")
    inflight = 0

    async def guarded(scope, receive, send):
        nonlocal inflight
        if scope["type"] != "http" or scope.get("path") == HEALTH_PATH:
            await inner(scope, receive, send)
            return
        wait = limiter.check(client_key(scope, header_name))
        if wait:
            await _refuse(send, 429, wait, "Too many requests from this address. Try again shortly.")
            return
        counted = not scope.get("path", "").startswith(LONG_LIVED_PREFIXES)
        if counted:
            if inflight >= cap:
                await _refuse(send, 503, 2, "The server is busy. Try again in a moment.")
                return
            inflight += 1
        try:
            await inner(scope, receive, send)
        finally:
            if counted:
                inflight -= 1

    return guarded
