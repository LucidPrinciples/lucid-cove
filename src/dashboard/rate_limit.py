"""Global HTTP rate limiting (#RATE1 / landscape-scan action item 2).

In-process sliding-window throttle at the middleware layer. No extra dependency.
Closes the audit gap where only SQL LIMIT clauses existed and no request throttle
protected /api/* (higher risk on multi-tenant hub; still worth having on single-
family boxes behind a reverse proxy).

Design notes:
- Keyed by client IP (X-Forwarded-For first hop when present, else ASGI client).
- Shared-secret service calls (X-Shared-Secret) bypass — internal fleet traffic
  must not fight the operator budget.
- Health/ping stay exempt so probes never trip 429.
- Static assets are not under /api/ and are skipped.
- In-memory only: one worker = one counter map. Multi-worker deploys get
  per-process budgets (documented). Redis can come later if hub scale needs it.
- Disable with RATE_LIMIT_ENABLED=0 for local soak tests.
"""

from __future__ import annotations

import hashlib
import hmac
import threading
import time
from collections import defaultdict, deque
from typing import Callable, Deque, Dict, Optional

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from src.env import env, env_bool, env_int


# Paths that must never 429 (probes + liveness). Keep tight.
_EXEMPT_PATHS = frozenset(
    {
        "/api/system/ping",
        "/api/system/health",
    }
)


class SlidingWindowCounter:
    """Per-key timestamps inside a fixed window. Thread-safe."""

    def __init__(self, window_seconds: int, max_keys: int = 20_000) -> None:
        self.window = max(1, int(window_seconds))
        self.max_keys = max_keys
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int) -> tuple[bool, int, int]:
        """Record one hit.

        Returns (allowed, remaining, retry_after_seconds).
        remaining is >= 0 when allowed; 0 when blocked.
        """
        now = time.monotonic()
        cutoff = now - self.window
        limit = max(1, int(limit))

        with self._lock:
            if key not in self._hits and len(self._hits) >= self.max_keys:
                # Opportunistic prune of expired-only keys, then drop oldest empty.
                self._evict_stale(cutoff)
                if len(self._hits) >= self.max_keys:
                    # Hard cap: refuse new keys by coalescing into overflow bucket
                    # rather than growing forever under IP rotation.
                    key = f"overflow:{hashlib.sha256(key.encode()).hexdigest()[:16]}"

            q = self._hits[key]
            while q and q[0] <= cutoff:
                q.popleft()

            if len(q) >= limit:
                retry = int(max(1, self.window - (now - q[0])))
                return False, 0, retry

            q.append(now)
            remaining = max(0, limit - len(q))
            return True, remaining, 0

    def _evict_stale(self, cutoff: float) -> None:
        dead = []
        for k, q in self._hits.items():
            while q and q[0] <= cutoff:
                q.popleft()
            if not q:
                dead.append(k)
        for k in dead:
            del self._hits[k]


def client_ip(request: Request) -> str:
    """Best-effort client IP behind Caddy/Tailscale.

    Prefer the left-most X-Forwarded-For hop (original client) when the proxy
    chain sets it. Fall back to the ASGI client host. Never raise.
    """
    xff = (request.headers.get("x-forwarded-for") or "").strip()
    if xff:
        # "client, proxy1, proxy2" — first is the originating client when
        # trusted proxies append. Cove sits behind our own Caddy.
        first = xff.split(",")[0].strip()
        if first:
            return first
    real = (request.headers.get("x-real-ip") or "").strip()
    if real:
        return real
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _shared_secret_ok(request: Request) -> bool:
    secret = env("SHARED_CONTAINER_SECRET", "")
    header = request.headers.get("X-Shared-Secret", "")
    if not secret or not header:
        return False
    try:
        return hmac.compare_digest(header, secret)
    except (TypeError, ValueError):
        return False


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Global /api/* throttle. See module docstring."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        self._counter: Optional[SlidingWindowCounter] = None
        self._cfg_loaded = False
        self._enabled = True
        self._limit = 120
        self._window = 60
        self._auth_limit = 30

    def _load_cfg(self) -> None:
        # Lazy so tests can monkeypatch env after import.
        if self._cfg_loaded:
            return
        self._enabled = env_bool("RATE_LIMIT_ENABLED", True)
        self._limit = max(1, env_int("RATE_LIMIT_PER_MINUTE", 120))
        self._window = max(1, env_int("RATE_LIMIT_WINDOW_SECONDS", 60))
        # Tighter budget for unauthenticated auth-surface POSTs (signin, magic link).
        self._auth_limit = max(1, env_int("RATE_LIMIT_AUTH_PER_MINUTE", 30))
        self._counter = SlidingWindowCounter(self._window)
        self._cfg_loaded = True

    def _limit_for(self, request: Request) -> int:
        path = request.url.path or ""
        # Auth entry points are the brute-force face — tighter than general API.
        if request.method in ("POST", "PUT", "PATCH") and path in {
            "/api/account/signin",
            "/api/account/create",
            "/api/account/magic-link",
            "/api/account/verify-magic-link",
            "/api/contact/submit",
        }:
            return self._auth_limit
        return self._limit

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        self._load_cfg()

        if not self._enabled or self._counter is None:
            return await call_next(request)

        if request.method == "OPTIONS":
            return await call_next(request)

        path = request.url.path or ""
        if not path.startswith("/api/"):
            return await call_next(request)
        if path in _EXEMPT_PATHS:
            return await call_next(request)
        if _shared_secret_ok(request):
            return await call_next(request)

        ip = client_ip(request)
        limit = self._limit_for(request)
        # Separate buckets so a signin flood cannot burn the general API budget
        # for the same IP (and vice versa).
        bucket = f"{ip}:auth" if limit == self._auth_limit else f"{ip}:api"
        allowed, remaining, retry_after = self._counter.hit(bucket, limit)

        if not allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Rate limit exceeded — retry shortly.",
                    "retry_after": retry_after,
                },
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Window": str(self._window),
                },
            )

        response = await call_next(request)
        # Surface budget on successful API responses (cheap debug / client backoff).
        try:
            response.headers["X-RateLimit-Limit"] = str(limit)
            response.headers["X-RateLimit-Remaining"] = str(remaining)
            response.headers["X-RateLimit-Window"] = str(self._window)
        except Exception:
            pass
        return response
