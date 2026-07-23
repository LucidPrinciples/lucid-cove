"""#RATE1 — global /api/* rate-limit middleware (landscape-scan action 2)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

ROOT = Path(__file__).resolve().parents[1]
APP_SRC = (ROOT / "src/dashboard/app.py").read_text(encoding="utf-8")
ENV_SRC = (ROOT / "src/env.py").read_text(encoding="utf-8")
RL_SRC = (ROOT / "src/dashboard/rate_limit.py").read_text(encoding="utf-8")


# ── Source guards (wired on main path) ───────────────────────────────────────


def test_rate_limit_middleware_registered_in_create_app():
    assert "RateLimitMiddleware" in APP_SRC
    assert "add_middleware(RateLimitMiddleware)" in APP_SRC


def test_rate_limit_env_knobs_registered():
    for name in (
        "RATE_LIMIT_ENABLED",
        "RATE_LIMIT_PER_MINUTE",
        "RATE_LIMIT_AUTH_PER_MINUTE",
        "RATE_LIMIT_WINDOW_SECONDS",
    ):
        assert f'EnvVar(\n        "{name}"' in ENV_SRC or f'EnvVar("{name}"' in ENV_SRC
    assert '"Security"' in ENV_SRC


def test_module_documents_in_memory_scope():
    assert "In-memory only" in RL_SRC or "in-memory" in RL_SRC.lower()
    assert "X-Shared-Secret" in RL_SRC


# ── Counter unit ─────────────────────────────────────────────────────────────


def test_sliding_window_blocks_after_limit():
    from src.dashboard.rate_limit import SlidingWindowCounter

    c = SlidingWindowCounter(window_seconds=60)
    for _ in range(3):
        ok, remaining, _retry = c.hit("ip1", limit=3)
        assert ok is True
    ok, remaining, retry = c.hit("ip1", limit=3)
    assert ok is False
    assert remaining == 0
    assert retry >= 1


def test_sliding_window_isolates_keys():
    from src.dashboard.rate_limit import SlidingWindowCounter

    c = SlidingWindowCounter(window_seconds=60)
    for _ in range(5):
        assert c.hit("a", limit=5)[0] is True
    assert c.hit("a", limit=5)[0] is False
    assert c.hit("b", limit=5)[0] is True


# ── client_ip ────────────────────────────────────────────────────────────────


class _Client:
    def __init__(self, host):
        self.host = host


def test_client_ip_prefers_forwarded_for():
    from src.dashboard.rate_limit import client_ip
    from unittest.mock import MagicMock

    req = MagicMock(spec=Request)
    req.headers = {"x-forwarded-for": "203.0.113.9, 10.0.0.1"}
    req.client = _Client("127.0.0.1")
    assert client_ip(req) == "203.0.113.9"


def test_client_ip_falls_back_to_client_host():
    from src.dashboard.rate_limit import client_ip
    from unittest.mock import MagicMock

    req = MagicMock(spec=Request)
    req.headers = {}
    req.client = _Client("198.51.100.4")
    assert client_ip(req) == "198.51.100.4"


# ── Middleware integration (minimal app) ─────────────────────────────────────


def _minimal_app(monkeypatch, **env_vars) -> TestClient:
    # Fresh env for deterministic limits
    monkeypatch.setenv("RATE_LIMIT_ENABLED", env_vars.get("enabled", "1"))
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", str(env_vars.get("limit", 5)))
    monkeypatch.setenv("RATE_LIMIT_AUTH_PER_MINUTE", str(env_vars.get("auth_limit", 2)))
    monkeypatch.setenv("RATE_LIMIT_WINDOW_SECONDS", str(env_vars.get("window", 60)))
    if "secret" in env_vars:
        monkeypatch.setenv("SHARED_CONTAINER_SECRET", env_vars["secret"])
    else:
        monkeypatch.delenv("SHARED_CONTAINER_SECRET", raising=False)

    # Re-import path: middleware reads env lazily on first request via _load_cfg
    from src.dashboard.rate_limit import RateLimitMiddleware

    app = FastAPI()
    app.add_middleware(RateLimitMiddleware)

    @app.get("/api/system/ping")
    def ping():
        return {"ok": True}

    @app.get("/api/thing")
    def thing():
        return {"ok": True}

    @app.post("/api/account/signin")
    def signin():
        return {"ok": True}

    @app.get("/static/x.js")
    def static_js():
        return {"ok": True}

    return TestClient(app)


def test_middleware_allows_under_limit(monkeypatch):
    client = _minimal_app(monkeypatch, limit=5)
    for _ in range(5):
        r = client.get("/api/thing")
        assert r.status_code == 200
        assert "X-RateLimit-Limit" in r.headers


def test_middleware_returns_429_over_limit(monkeypatch):
    client = _minimal_app(monkeypatch, limit=3)
    for _ in range(3):
        assert client.get("/api/thing").status_code == 200
    r = client.get("/api/thing")
    assert r.status_code == 429
    assert r.json()["detail"].startswith("Rate limit exceeded")
    assert r.headers.get("Retry-After")
    assert r.headers.get("X-RateLimit-Remaining") == "0"


def test_middleware_exempts_health_ping(monkeypatch):
    client = _minimal_app(monkeypatch, limit=2)
    # Burn the general budget
    assert client.get("/api/thing").status_code == 200
    assert client.get("/api/thing").status_code == 200
    assert client.get("/api/thing").status_code == 429
    # Probes still pass
    assert client.get("/api/system/ping").status_code == 200


def test_middleware_skips_static(monkeypatch):
    client = _minimal_app(monkeypatch, limit=1)
    assert client.get("/api/thing").status_code == 200
    assert client.get("/api/thing").status_code == 429
    assert client.get("/static/x.js").status_code == 200


def test_middleware_auth_bucket_tighter(monkeypatch):
    client = _minimal_app(monkeypatch, limit=50, auth_limit=2)
    assert client.post("/api/account/signin").status_code == 200
    assert client.post("/api/account/signin").status_code == 200
    r = client.post("/api/account/signin")
    assert r.status_code == 429
    # General API budget still intact
    assert client.get("/api/thing").status_code == 200


def test_middleware_shared_secret_bypasses(monkeypatch):
    client = _minimal_app(monkeypatch, limit=1, secret="s3cret-test")
    assert client.get("/api/thing").status_code == 200
    assert client.get("/api/thing").status_code == 429
    r = client.get("/api/thing", headers={"X-Shared-Secret": "s3cret-test"})
    assert r.status_code == 200


def test_middleware_disabled(monkeypatch):
    client = _minimal_app(monkeypatch, enabled="0", limit=1)
    for _ in range(5):
        assert client.get("/api/thing").status_code == 200
