"""A mistyped signup can correct its email before anyone signs in.

The public hub still does not return the sign-in link.
"""
import sys
import types
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

import src.dashboard.routes.account as account
import src.dashboard.routes.email as email
import src.dashboard.routes.presence as presence
import src.dashboard.routes.registry as registry

AID = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"
_HUB = "app.lucidtuner.com"
_EMAIL_409 = "An account with this email already exists. Try signing in instead."
_USER_409 = "This username is taken. Try another one."


class _Result:
    def __init__(self, row):
        self._row = row

    async def fetchone(self):
        return self._row


class _Conn:
    def __init__(self, accounts, later_ids=None):
        self.accounts = list(accounts)
        self.later_ids = {str(i) for i in (later_ids or [])}
        self.sql = []
        self.params = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, sql, params=None):
        compact = " ".join(str(sql).split())
        self.sql.append(compact)
        self.params.append(params)
        params = params or ()
        if compact.startswith("SELECT id FROM accounts WHERE email"):
            row = next((a for a in self.accounts if a["email"] == params[0]), None)
            return _Result({"id": row["id"]} if row else None)
        if compact.startswith("SELECT id FROM accounts WHERE username"):
            row = next((a for a in self.accounts if a["username"] == params[0]), None)
            return _Result({"id": row["id"]} if row else None)
        if "FROM accounts WHERE id = %s" in compact and "display_name" in compact:
            row = next((a for a in self.accounts if str(a["id"]) == str(params[0])), None)
            return _Result(row)
        if "FROM auth_sessions" in compact:
            hit = str(params[0]) in self.later_ids
            return _Result({"ok": 1} if hit else None)
        return _Result(None)


class _URL:
    scheme = "https"


class _Request:
    def __init__(self, body):
        self._body = body
        self.headers = {"host": _HUB, "x-forwarded-proto": "https"}
        self.url = _URL()

    async def json(self):
        return self._body


def _account(**over):
    row = {
        "id": AID,
        "username": "ada",
        "email": "typo@example.com",
        "display_name": "Ada",
        "created_at": datetime.now(timezone.utc),
        "last_access": None,
        "auth_token": "old-hash",
    }
    row.update(over)
    return row


def _body(**over):
    body = {
        "email": "ada@example.com",
        "username": "ada",
        "display_name": "Someone Else",
        "replace_presence_id": AID,
    }
    body.update(over)
    return body


def _wire(monkeypatch, conn, *, sent=True):
    sent_to = []
    brevo = []
    sessions = []
    handles = []
    posts = []

    async def _send(addr, link, is_signup=False, **_k):
        sent_to.append((addr, is_signup, link))
        return sent

    async def _brevo(addr, name=""):
        brevo.append((addr, name))
        return True

    async def _session(_conn, account_id, token_hash, label=None):
        sessions.append((str(account_id), token_hash, label))

    async def _handle(_conn, username, referred_by=""):
        handles.append(username)

    db_mod = types.ModuleType("src.memory.database")
    db_mod.get_db = lambda: conn
    monkeypatch.setitem(sys.modules, "src.memory.database", db_mod)
    monkeypatch.setattr(account, "COVE_MODE", "multi")
    monkeypatch.setattr(account, "UPGRADE_SECRET", "sek")
    monkeypatch.setenv("MARKETPLACE_API_URL", "http://market.test")
    monkeypatch.setattr(email, "send_signin_link", _send)
    monkeypatch.setattr(email, "add_to_brevo_list", _brevo)
    monkeypatch.setattr(presence, "_create_session", _session)
    monkeypatch.setattr(registry, "reserve_handle", _handle)

    class _Client:
        def __init__(self, timeout=8):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, headers=None, json=None):
            posts.append(json)
            return types.SimpleNamespace(status_code=200)

    httpx_mod = types.ModuleType("httpx")
    httpx_mod.AsyncClient = _Client
    monkeypatch.setitem(sys.modules, "httpx", httpx_mod)
    return {
        "sent": sent_to,
        "brevo": brevo,
        "sessions": sessions,
        "handles": handles,
        "posts": posts,
    }


async def _call(body):
    try:
        result = await account.create_account(_Request(body))
    except HTTPException as exc:
        return exc.status_code, {"detail": exc.detail}
    return 200, result


@pytest.mark.asyncio
async def test_replace_presence_id_corrects_email_and_kills_old_token(monkeypatch):
    conn = _Conn([_account()])
    seen = _wire(monkeypatch, conn)
    status, body = await _call(_body())

    assert status == 200
    assert body["ok"] is True
    assert body["presence_id"] == AID
    assert body["display_name"] == "Ada"
    assert body["email_sent"] is True
    assert "signin_link" not in body

    updates = [
        (sql, params) for sql, params in zip(conn.sql, conn.params)
        if sql.startswith("UPDATE accounts")
    ]
    assert len(updates) == 1
    _sql, params = updates[0]
    assert params[0] == "ada@example.com"
    assert params[1] != "old-hash"
    assert str(params[2]) == AID
    assert any("active = FALSE" in sql for sql in conn.sql)
    assert seen["sessions"] == [(AID, params[1], "signup")]
    assert seen["handles"] == []
    assert seen["sent"][0][0] == "ada@example.com"
    assert seen["sent"][0][1] is True
    assert seen["brevo"] == [("ada@example.com", "Ada")]
    assert seen["posts"] == [{
        "external_id": AID,
        "email": "ada@example.com",
        "name": "Ada",
    }]
    corrected = [
        params for sql, params in zip(conn.sql, conn.params)
        if "email_corrected" in sql
    ]
    assert len(corrected) == 1
    assert "typo@example.com" in corrected[0][1]
    assert "ada@example.com" in corrected[0][1]
    assert not any("VALUES (%s, 'signup'" in sql for sql in conn.sql)
    assert not any(sql.startswith("INSERT INTO accounts") for sql in conn.sql)


@pytest.mark.asyncio
async def test_wrong_replace_id_is_username_409(monkeypatch):
    conn = _Conn([_account()])
    seen = _wire(monkeypatch, conn)
    status, body = await _call(_body(replace_presence_id=OTHER))
    assert status == 409
    assert body["detail"] == _USER_409
    assert not any(sql.startswith("UPDATE accounts") for sql in conn.sql)
    assert seen["sent"] == []


@pytest.mark.asyncio
async def test_signed_in_account_is_not_replaced(monkeypatch):
    conn = _Conn([_account(last_access=datetime.now(timezone.utc))])
    _wire(monkeypatch, conn)
    status, body = await _call(_body())
    assert status == 409
    assert body["detail"] == _USER_409
    assert not any(sql.startswith("UPDATE accounts") for sql in conn.sql)


@pytest.mark.asyncio
async def test_later_session_is_not_replaced(monkeypatch):
    conn = _Conn([_account()], later_ids=[AID])
    _wire(monkeypatch, conn)
    status, body = await _call(_body())
    assert status == 409
    assert body["detail"] == _USER_409


@pytest.mark.asyncio
async def test_account_older_than_a_day_is_not_replaced(monkeypatch):
    old = datetime.now(timezone.utc) - timedelta(hours=25)
    conn = _Conn([_account(created_at=old)])
    _wire(monkeypatch, conn)
    status, body = await _call(_body())
    assert status == 409
    assert body["detail"] == _USER_409


@pytest.mark.asyncio
async def test_duplicate_without_replace_id_is_409(monkeypatch):
    conn = _Conn([_account()])
    seen = _wire(monkeypatch, conn)
    status, body = await _call(_body(replace_presence_id=""))
    assert status == 409
    assert body["detail"] == _USER_409
    assert seen["sent"] == []
    assert not any("email_corrected" in sql for sql in conn.sql)


@pytest.mark.asyncio
async def test_email_owned_by_someone_else_stays_409(monkeypatch):
    other = _account(id=OTHER, username="bea", email="ada@example.com", display_name="Bea")
    conn = _Conn([_account(), other])
    _wire(monkeypatch, conn)
    status, body = await _call(_body())
    assert status == 409
    assert body["detail"] == _EMAIL_409
    assert not any(sql.startswith("UPDATE accounts") for sql in conn.sql)
