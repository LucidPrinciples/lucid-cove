"""HAVENMC2 — hub admin hard-purge of deactivated accounts (no live DB)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.dashboard.routes import account as account_mod


ACCOUNT_SRC = account_mod.__file__ and open(account_mod.__file__, encoding="utf-8").read()


def test_purge_route_and_keep_list_are_source_locked():
    assert "/api/admin/accounts/{account_id}" in ACCOUNT_SRC
    assert "async def admin_purge_account" in ACCOUNT_SRC
    assert "Kept accounts cannot be purged" in ACCOUNT_SRC
    assert "ADMIN_PURGE_KEEP" in ACCOUNT_SRC
    assert "PURGE_DEFAULT_AGE_HOURS = 720" in ACCOUNT_SRC
    assert "DELETE FROM registry_handles" in ACCOUNT_SRC
    assert "Handle still attached to cove" in ACCOUNT_SRC


class _FakeResult:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows if rows is not None else ([] if row is None else [row])

    async def fetchone(self):
        return self._row

    async def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, account=None, handle=None, cove=None, hours=800.0):
        self.calls = []
        self.account = account
        self.handle = handle
        self.cove = cove
        self.hours = hours

    async def execute(self, sql, params=None):
        self.calls.append((sql, params))
        sql_l = " ".join(sql.lower().split())
        if "from accounts where id" in sql_l:
            return _FakeResult(self.account)
        if "extract(epoch" in sql_l:
            return _FakeResult({"hours": self.hours})
        if "from registry_handles" in sql_l:
            return _FakeResult(self.handle)
        if "from registry_coves" in sql_l:
            return _FakeResult(self.cove)
        return _FakeResult(None)


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


def _account(**over):
    now = datetime.now(timezone.utc) - timedelta(hours=800)
    base = {
        "id": "dddddddd-dddd-dddd-dddd-dddddddddddd",
        "email": None,
        "username": "zzdiagonly99",
        "active": False,
        "stripe_customer_id": None,
        "deactivated_at": now,
        "deactivated_by": "jag",
        "deactivated_reason": "junk-pass 2026-10-04",
    }
    base.update(over)
    return base


@pytest.mark.asyncio
async def test_purge_requires_secret(monkeypatch):
    monkeypatch.setattr(account_mod, "UPGRADE_SECRET", "hub-secret")
    transport = ASGITransport(app=_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.request(
            "DELETE",
            "/api/admin/accounts/dddddddd-dddd-dddd-dddd-dddddddddddd",
            json={"confirm": "zzdiagonly99"},
        )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_purge_refuses_kept_username(monkeypatch):
    monkeypatch.setattr(account_mod, "UPGRADE_SECRET", "hub-secret")
    monkeypatch.setenv("ADMIN_PURGE_KEEP", "keptuser")
    conn = _FakeConn(account=_account(username="keptuser", email="kept@example.com"))
    monkeypatch.setattr("src.memory.database.get_db", lambda: _DBCM(conn))
    transport = ASGITransport(app=_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.request(
            "DELETE",
            "/api/admin/accounts/dddddddd-dddd-dddd-dddd-dddddddddddd",
            headers={"X-Shared-Secret": "hub-secret"},
            json={"confirm": "keptuser", "actor": "ops"},
        )
    assert res.status_code == 403
    assert "Kept" in res.json()["detail"]
    assert not any("DELETE FROM accounts" in c[0] for c in conn.calls)


@pytest.mark.asyncio
async def test_purge_refuses_active(monkeypatch):
    monkeypatch.setattr(account_mod, "UPGRADE_SECRET", "hub-secret")
    conn = _FakeConn(account=_account(active=True))
    monkeypatch.setattr("src.memory.database.get_db", lambda: _DBCM(conn))
    transport = ASGITransport(app=_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.request(
            "DELETE",
            "/api/admin/accounts/dddddddd-dddd-dddd-dddd-dddddddddddd",
            headers={"X-Shared-Secret": "hub-secret"},
            json={"confirm": "zzdiagonly99"},
        )
    assert res.status_code == 409
    assert "deactivated" in res.json()["detail"]


@pytest.mark.asyncio
async def test_purge_refuses_stripe(monkeypatch):
    monkeypatch.setattr(account_mod, "UPGRADE_SECRET", "hub-secret")
    conn = _FakeConn(account=_account(stripe_customer_id="cus_x"))
    monkeypatch.setattr("src.memory.database.get_db", lambda: _DBCM(conn))
    transport = ASGITransport(app=_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.request(
            "DELETE",
            "/api/admin/accounts/dddddddd-dddd-dddd-dddd-dddddddddddd",
            headers={"X-Shared-Secret": "hub-secret"},
            json={"confirm": "zzdiagonly99"},
        )
    assert res.status_code == 409
    assert "Stripe" in res.json()["detail"]


@pytest.mark.asyncio
async def test_purge_refuses_cooling(monkeypatch):
    monkeypatch.setattr(account_mod, "UPGRADE_SECRET", "hub-secret")
    conn = _FakeConn(account=_account(), hours=2.0)
    monkeypatch.setattr("src.memory.database.get_db", lambda: _DBCM(conn))
    transport = ASGITransport(app=_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.request(
            "DELETE",
            "/api/admin/accounts/dddddddd-dddd-dddd-dddd-dddddddddddd",
            headers={"X-Shared-Secret": "hub-secret"},
            json={"confirm": "zzdiagonly99", "min_age_hours": 24},
        )
    assert res.status_code == 409
    assert "Cooling" in res.json()["detail"]


@pytest.mark.asyncio
async def test_purge_refuses_attached_cove(monkeypatch):
    monkeypatch.setattr(account_mod, "UPGRADE_SECRET", "hub-secret")
    conn = _FakeConn(
        account=_account(username="chords1"),
        handle={"handle": "chords1", "cove_id": "lucidcove-52403c754e3a6144"},
        cove={"cove_id": "lucidcove-52403c754e3a6144", "name": "diag"},
        hours=800.0,
    )
    monkeypatch.setattr("src.memory.database.get_db", lambda: _DBCM(conn))
    transport = ASGITransport(app=_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.request(
            "DELETE",
            "/api/admin/accounts/dddddddd-dddd-dddd-dddd-dddddddddddd",
            headers={"X-Shared-Secret": "hub-secret"},
            json={"confirm": "chords1", "min_age_hours": 0},
        )
    assert res.status_code == 409
    assert "cove" in res.json()["detail"]
    assert not any("DELETE FROM accounts" in c[0] for c in conn.calls)


@pytest.mark.asyncio
async def test_purge_deletes_account_and_orphan_handle(monkeypatch):
    monkeypatch.setattr(account_mod, "UPGRADE_SECRET", "hub-secret")
    conn = _FakeConn(
        account=_account(),
        handle={"handle": "zzdiagonly99", "cove_id": None},
        hours=800.0,
    )
    monkeypatch.setattr("src.memory.database.get_db", lambda: _DBCM(conn))
    transport = ASGITransport(app=_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.request(
            "DELETE",
            "/api/admin/accounts/dddddddd-dddd-dddd-dddd-dddddddddddd",
            headers={"X-Shared-Secret": "hub-secret"},
            json={
                "confirm": "zzdiagonly99",
                "actor": "jag",
                "reason": "junk-pass purge",
                "min_age_hours": 0,
            },
        )
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["purged"] is True
    assert body["username"] == "zzdiagonly99"
    sqls = " ".join(c[0] for c in conn.calls)
    assert "DELETE FROM tuning_events" in sqls
    assert "UPDATE contact_messages SET account_id = NULL" in sqls
    assert "DELETE FROM registry_handles" in sqls
    assert "DELETE FROM accounts WHERE id" in sqls
