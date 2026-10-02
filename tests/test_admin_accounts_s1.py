"""HAVENMC2 S1 — hub admin accounts list + deactivate (no live DB)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from src.dashboard.routes import account as account_mod


ROOT = Path(__file__).resolve().parents[1]
ACCOUNT_SRC = (ROOT / "src/dashboard/routes/account.py").read_text(encoding="utf-8")
MIG_SRC = (ROOT / "docker/migrations/050_account_deactivate.sql").read_text(
    encoding="utf-8"
)
REGISTRY_SRC = (ROOT / "src/dashboard/routes/registry.py").read_text(encoding="utf-8")


def test_migration_050_adds_deactivate_columns():
    assert "ADD COLUMN IF NOT EXISTS deactivated_at" in MIG_SRC
    assert "ADD COLUMN IF NOT EXISTS deactivated_by" in MIG_SRC
    assert "ADD COLUMN IF NOT EXISTS deactivated_reason" in MIG_SRC


def test_admin_list_accepts_filters_and_header_secret():
    assert 'include_inactive: bool = False' in ACCOUNT_SRC
    assert 'sort: str = "newest"' in ACCOUNT_SRC
    assert "X-Shared-Secret" in ACCOUNT_SRC
    assert "/api/admin/accounts/{account_id}/active" in ACCOUNT_SRC
    assert "UPDATE auth_sessions SET active = FALSE" in ACCOUNT_SRC
    assert '"newest": "created_at DESC NULLS LAST"' in ACCOUNT_SRC


def test_resolve_handle_skips_inactive_accounts():
    assert (
        "SELECT matrix_username FROM accounts WHERE lower(username) = %s AND active = TRUE"
        in REGISTRY_SRC
    )


class _FakeResult:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows if rows is not None else ([] if row is None else [row])

    async def fetchone(self):
        return self._row

    async def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, rows=None, account=None):
        self.calls = []
        self.rows = rows or []
        self.account = account

    async def execute(self, sql, params=None):
        self.calls.append((sql, params))
        sql_l = " ".join(sql.lower().split())
        if "count(*) filter" in sql_l:
            return _FakeResult({"active_count": 2, "deactivated_count": 1})
        if "from accounts" in sql_l and "order by" in sql_l:
            return _FakeResult(rows=self.rows)
        if "from accounts where id" in sql_l:
            return _FakeResult(self.account)
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


@pytest.mark.asyncio
async def test_admin_list_requires_secret(monkeypatch):
    monkeypatch.setattr(account_mod, "UPGRADE_SECRET", "hub-secret")
    transport = ASGITransport(app=_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/admin/accounts")
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_admin_list_hides_inactive_and_sorts_newest(monkeypatch):
    monkeypatch.setattr(account_mod, "UPGRADE_SECRET", "hub-secret")
    now = datetime.now(timezone.utc)
    rows = [
        {
            "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "display_name": "Ada",
            "username": "ada",
            "email": "ada@example.com",
            "tier": "free",
            "referral_code": "LP-AAAAAA",
            "referred_by": None,
            "stripe_customer_id": None,
            "active": True,
            "created_at": now,
            "last_access": now,
            "updated_at": now,
            "deactivated_at": None,
            "deactivated_by": None,
            "deactivated_reason": None,
        }
    ]
    conn = _FakeConn(rows=rows)
    monkeypatch.setattr(account_mod, "get_db", lambda: _DBCM(conn), raising=False)
    monkeypatch.setattr("src.memory.database.get_db", lambda: _DBCM(conn))

    transport = ASGITransport(app=_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get(
            "/api/admin/accounts",
            headers={"X-Shared-Secret": "hub-secret"},
        )
    assert res.status_code == 200
    body = res.json()
    assert body["count"] == 1
    assert body["active_count"] == 2
    assert body["deactivated_count"] == 1
    assert body["accounts"][0]["username"] == "ada"
    list_sql = conn.calls[1][0]
    assert "active = TRUE" in list_sql
    assert "created_at DESC" in list_sql
    assert "deactivated_at" in list_sql


@pytest.mark.asyncio
async def test_admin_deactivate_ends_sessions(monkeypatch):
    monkeypatch.setattr(account_mod, "UPGRADE_SECRET", "hub-secret")
    account = {
        "id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        "email": "bob@example.com",
        "username": "bob",
        "active": True,
        "stripe_customer_id": "cus_test",
    }
    conn = _FakeConn(account=account)
    monkeypatch.setattr("src.memory.database.get_db", lambda: _DBCM(conn))

    transport = ASGITransport(app=_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.patch(
            "/api/admin/accounts/bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb/active",
            headers={"X-Shared-Secret": "hub-secret"},
            json={"active": False, "reason": "test junk", "actor": "jason"},
        )
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["active"] is False
    assert body["stripe_customer_id"] == "cus_test"
    sqls = " ".join(c[0] for c in conn.calls)
    assert "active = FALSE" in sqls
    assert "UPDATE auth_sessions SET active = FALSE" in sqls
    assert "deactivated_reason" in sqls


@pytest.mark.asyncio
async def test_admin_reactivate_clears_fields(monkeypatch):
    monkeypatch.setattr(account_mod, "UPGRADE_SECRET", "hub-secret")
    account = {
        "id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
        "email": "cara@example.com",
        "username": "cara",
        "active": False,
        "stripe_customer_id": None,
    }
    conn = _FakeConn(account=account)
    monkeypatch.setattr("src.memory.database.get_db", lambda: _DBCM(conn))

    transport = ASGITransport(app=_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.patch(
            "/api/admin/accounts/cccccccc-cccc-cccc-cccc-cccccccccccc/active",
            headers={"X-Shared-Secret": "hub-secret"},
            json={"active": True, "actor": "jason"},
        )
    assert res.status_code == 200
    body = res.json()
    assert body["active"] is True
    sqls = " ".join(c[0] for c in conn.calls)
    assert "deactivated_at = NULL" in sqls
    assert "UPDATE auth_sessions" not in sqls
