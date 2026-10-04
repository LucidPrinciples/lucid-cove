"""HAVENMC2 S2 — hub admin registry list (no live DB, no health pings)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.dashboard.routes import account as account_mod


ROOT = Path(__file__).resolve().parents[1]
ACCOUNT_SRC = (ROOT / "src/dashboard/routes/account.py").read_text(encoding="utf-8")


def test_admin_registry_route_is_header_secret_and_lists_tables():
    assert "/api/admin/registry" in ACCOUNT_SRC
    assert "async def admin_list_registry" in ACCOUNT_SRC
    assert "FROM registry_coves" in ACCOUNT_SRC
    assert "FROM registry_havens" in ACCOUNT_SRC
    assert "FROM registry_handles" in ACCOUNT_SRC
    assert "LEFT JOIN accounts" in ACCOUNT_SRC
    assert "account_active" in ACCOUNT_SRC
    assert "/api/haven/info" not in ACCOUNT_SRC
    assert "response_ms" not in ACCOUNT_SRC


class _FakeResult:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows if rows is not None else ([] if row is None else [row])

    async def fetchone(self):
        return self._row

    async def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, coves=None, havens=None, handles=None):
        self.calls = []
        self.coves = coves or []
        self.havens = havens or []
        self.handles = handles or []

    async def execute(self, sql, params=None):
        self.calls.append((sql, params))
        sql_l = " ".join(sql.lower().split())
        if "from registry_coves" in sql_l:
            return _FakeResult(rows=self.coves)
        if "from registry_havens" in sql_l:
            return _FakeResult(rows=self.havens)
        if "from registry_handles" in sql_l:
            return _FakeResult(rows=self.handles)
        return _FakeResult(rows=[])


class _DBCM:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *a):
        return False


def _app():
    app = FastAPI()
    app.include_router(account_mod.router)
    return app


@pytest.mark.asyncio
async def test_admin_registry_requires_secret(monkeypatch):
    monkeypatch.setattr(account_mod, "UPGRADE_SECRET", "hub-secret")
    transport = ASGITransport(app=_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/admin/registry")
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_admin_registry_lists_coves_including_inactive_owner(monkeypatch):
    monkeypatch.setattr(account_mod, "UPGRADE_SECRET", "hub-secret")
    now = datetime.now(timezone.utc)
    coves = [
        {
            "cove_id": "lucidcove-52403c754e3a6144",
            "name": "Mindstretch",
            "owner_handle": "chords1",
            "domain": "mindstretch.lucidcove.org",
            "homeserver": "matrix.mindstretch.lucidcove.org",
            "space_id": "!space:matrix.mindstretch.lucidcove.org",
            "mesh_ip": None,
            "created_at": now,
            "last_seen": None,
            "updated_at": now,
        },
        {
            "cove_id": "clearfield",
            "name": "Clearfield",
            "owner_handle": "jag",
            "domain": "clearfield.lucidcove.org",
            "homeserver": "matrix.clearfield.lucidcove.org",
            "space_id": None,
            "mesh_ip": None,
            "created_at": now,
            "last_seen": now,
            "updated_at": now,
        },
    ]
    handles = [
        {
            "handle": "chords1",
            "cove_id": "lucidcove-52403c754e3a6144",
            "matrix_user": "@chords1:matrix.mindstretch.lucidcove.org",
            "referred_by": None,
            "last_seen": now,
            "created_at": now,
            "account_id": "11111111-1111-1111-1111-111111111111",
            "display_name": "Chords1",
            "email": "chords1@example.com",
            "tier": "free",
            "account_active": False,
        },
        {
            "handle": "jag",
            "cove_id": "clearfield",
            "matrix_user": None,
            "referred_by": None,
            "last_seen": now,
            "created_at": now,
            "account_id": "22222222-2222-2222-2222-222222222222",
            "display_name": "Jag",
            "email": "jag@example.com",
            "tier": "pro",
            "account_active": True,
        },
        {
            "handle": "cotf",
            "cove_id": None,
            "matrix_user": None,
            "referred_by": None,
            "last_seen": None,
            "created_at": now,
            "account_id": "33333333-3333-3333-3333-333333333333",
            "display_name": "Cotf",
            "email": "cotf@example.com",
            "tier": "free",
            "account_active": True,
        },
    ]
    conn = _FakeConn(coves=coves, havens=[], handles=handles)
    monkeypatch.setattr("src.memory.database.get_db", lambda: _DBCM(conn))

    transport = ASGITransport(app=_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get(
            "/api/admin/registry",
            headers={"X-Shared-Secret": "hub-secret"},
        )
    assert res.status_code == 200
    body = res.json()
    assert body["cove_count"] == 2
    assert body["handle_count"] == 3
    assert body["haven_count"] == 0
    names = [c["name"] for c in body["coves"]]
    assert "Mindstretch" in names
    mind = next(c for c in body["coves"] if c["name"] == "Mindstretch")
    assert mind["owner_handle"] == "chords1"
    assert mind["domain"] == "mindstretch.lucidcove.org"
    assert "chords1" in mind["handles"]
    chords = next(h for h in body["handles"] if h["handle"] == "chords1")
    assert chords["account_active"] is False
    assert chords["cove_name"] == "Mindstretch"
    unattached = next(h for h in body["handles"] if h["handle"] == "cotf")
    assert unattached["cove_id"] is None
    sqls = " ".join(c[0] for c in conn.calls)
    assert "FROM registry_coves" in sqls
    assert "FROM registry_havens" in sqls
    assert "FROM registry_handles" in sqls
