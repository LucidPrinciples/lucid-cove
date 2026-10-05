"""HAVENMC2 S5 — hub admin/contact accept X-Shared-Secret (query fallback one release)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.dashboard.routes import account as account_mod
from src.dashboard.routes import contact as contact_mod


ROOT = Path(__file__).resolve().parents[1]
ACCOUNT_SRC = (ROOT / "src/dashboard/routes/account.py").read_text(encoding="utf-8")
CONTACT_SRC = (ROOT / "src/dashboard/routes/contact.py").read_text(encoding="utf-8")


def test_contact_prefers_header():
    assert "def _require_contact_secret" in CONTACT_SRC
    assert 'request.headers.get("X-Shared-Secret")' in CONTACT_SRC
    assert "HAVENMC2-S5" in CONTACT_SRC
    assert "_require_contact_secret(request)" in CONTACT_SRC


def test_stats_and_activity_use_upgrade_helper():
    assert "async def admin_stats" in ACCOUNT_SRC
    assert "async def admin_activity" in ACCOUNT_SRC
    assert ACCOUNT_SRC.count("_require_upgrade_secret(request, secret)") >= 2
    assert 'header = (request.headers.get("X-Shared-Secret") or "").strip()' in ACCOUNT_SRC


class _FakeResult:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows if rows is not None else ([] if row is None else [row])

    async def fetchone(self):
        return self._row

    async def fetchall(self):
        return self._rows


class _FakeConn:
    async def execute(self, sql, params=None):
        sql_l = " ".join(sql.lower().split())
        if "from contact_messages" in sql_l and "select id" in sql_l:
            return _FakeResult(rows=[])
        if "group by tier" in sql_l:
            return _FakeResult(rows=[])
        if "count(*)" in sql_l:
            return _FakeResult({"total": 0})
        return _FakeResult({"total": 0})


class _DBCM:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *a):
        return False


def _contact_app():
    app = FastAPI()
    app.include_router(contact_mod.router)
    return app


def _account_app():
    app = FastAPI()
    app.include_router(account_mod.router)
    return app


@pytest.mark.asyncio
async def test_contact_messages_403_without_auth(monkeypatch):
    monkeypatch.setattr(contact_mod, "CONTACT_SECRET", "hub-token")
    monkeypatch.setattr(contact_mod, "shared_hub_url", lambda: "")
    transport = ASGITransport(app=_contact_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/contact/messages")
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_contact_messages_accepts_header(monkeypatch):
    monkeypatch.setattr(contact_mod, "CONTACT_SECRET", "hub-token")
    monkeypatch.setattr(contact_mod, "shared_hub_url", lambda: "")
    import src.memory.database as db_mod

    monkeypatch.setattr(db_mod, "get_db", lambda: _DBCM(_FakeConn()))
    transport = ASGITransport(app=_contact_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get(
            "/api/contact/messages",
            headers={"X-Shared-Secret": "hub-token"},
        )
    assert res.status_code == 200
    assert res.json().get("count") == 0


@pytest.mark.asyncio
async def test_contact_messages_query_fallback_still_works(monkeypatch):
    monkeypatch.setattr(contact_mod, "CONTACT_SECRET", "hub-token")
    monkeypatch.setattr(contact_mod, "shared_hub_url", lambda: "")
    import src.memory.database as db_mod

    monkeypatch.setattr(db_mod, "get_db", lambda: _DBCM(_FakeConn()))
    transport = ASGITransport(app=_contact_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/contact/messages", params={"secret": "hub-token"})
    assert res.status_code == 200


@pytest.mark.asyncio
async def test_admin_stats_accepts_header(monkeypatch):
    monkeypatch.setattr(account_mod, "UPGRADE_SECRET", "hub-token")
    import src.memory.database as db_mod

    monkeypatch.setattr(db_mod, "get_db", lambda: _DBCM(_FakeConn()))
    transport = ASGITransport(app=_account_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get(
            "/api/admin/stats",
            headers={"X-Shared-Secret": "hub-token"},
        )
    assert res.status_code == 200
    assert "total_accounts" in res.json()


@pytest.mark.asyncio
async def test_admin_stats_403_without_auth(monkeypatch):
    monkeypatch.setattr(account_mod, "UPGRADE_SECRET", "hub-token")
    transport = ASGITransport(app=_account_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/admin/stats")
    assert res.status_code == 403
