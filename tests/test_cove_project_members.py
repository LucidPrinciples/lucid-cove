"""Cove projects + project_members attach (manager chat ownership)."""

from pathlib import Path

import pytest

from src.tools import project_tools as pt


ROOT = Path(__file__).resolve().parents[1]


def test_migration_043_defines_project_members():
    sql = (ROOT / "docker/migrations/043_project_members.sql").read_text()
    assert "CREATE TABLE IF NOT EXISTS project_members" in sql
    assert "presence_id" in sql


def test_chat_manager_uses_viewer_not_member_board():
    src = (ROOT / "src/dashboard/routes/chat.py").read_text()
    assert "set_request_project_viewer" in src
    assert "set_team_nc_creds" in src
    # Must NOT reintroduce member-board bind for manager channels
    assert "_mgr_uses_member_space" not in src


def test_attach_tool_registered():
    assert hasattr(pt, "attach_project_member")
    assert hasattr(pt, "set_request_project_viewer")
    assert hasattr(pt, "_attach_presence_to_project")


def test_api_members_routes_exist():
    src = (ROOT / "src/dashboard/routes/projects.py").read_text()
    assert "/api/projects/{project_id}/members" in src
    assert "project_members" in src
    assert "_is_cove_admin" in src


@pytest.mark.asyncio
async def test_create_project_cove_auto_attaches_viewer(monkeypatch):
    class _FakeResult:
        def __init__(self, row=None):
            self._row = row

        async def fetchone(self):
            return self._row

    class _FakeConn:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params=None):
            self.calls.append((sql, params))
            sql_l = " ".join(sql.lower().split())
            if sql_l.startswith("select id from projects where slug"):
                return _FakeResult(None)
            if "insert into projects" in sql_l:
                return _FakeResult({"id": 9, "slug": params[1], "name": params[2]})
            if "insert into project_members" in sql_l:
                return _FakeResult(None)
            return _FakeResult(None)

    class _DBCM:
        def __init__(self, conn):
            self.conn = conn

        async def __aenter__(self):
            return self.conn

        async def __aexit__(self, *a):
            return False

    conn = _FakeConn()
    monkeypatch.setattr(pt, "get_db", lambda: _DBCM(conn))
    monkeypatch.setattr(pt, "_get_operator_id", lambda: "op")
    async def _nf(_n):
        return " — folder Projects/X/ ready"
    monkeypatch.setattr(pt, "_ensure_project_nc_folder", _nf)
    async def _att(pid, presence_id, role="work", display_name=""):
        return f"attached-{presence_id}"
    monkeypatch.setattr(pt, "_attach_presence_to_project", _att)

    # Cove board (no presence bind) + viewer
    vtok = pt.set_request_project_viewer("pres-member-1")
    try:
        fn = pt.create_project
        coro = fn.coroutine if hasattr(fn, "coroutine") else fn
        out = await coro(name="Shared Job", description="", goals="")
        assert "Cove" in out
        assert "attached-pres-member-1" in out
        insert = [c for c in conn.calls if "INSERT INTO projects" in c[0]]
        assert insert
        assert insert[0][1][0] is None  # presence_id NULL
    finally:
        pt.clear_request_project_presence(vtok)


@pytest.mark.asyncio
async def test_create_project_personal_no_auto_attach(monkeypatch):
    class _FakeResult:
        def __init__(self, row=None):
            self._row = row

        async def fetchone(self):
            return self._row

    class _FakeConn:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params=None):
            self.calls.append((sql, params))
            sql_l = " ".join(sql.lower().split())
            if sql_l.startswith("select id from projects where slug"):
                return _FakeResult(None)
            if "insert into projects" in sql_l:
                return _FakeResult({"id": 3, "slug": params[1], "name": params[2]})
            return _FakeResult(None)

    class _DBCM:
        def __init__(self, conn):
            self.conn = conn

        async def __aenter__(self):
            return self.conn

        async def __aexit__(self, *a):
            return False

    conn = _FakeConn()
    monkeypatch.setattr(pt, "get_db", lambda: _DBCM(conn))
    monkeypatch.setattr(pt, "_get_operator_id", lambda: "op")
    monkeypatch.setattr(pt, "_ensure_project_nc_folder", lambda n: _async_str(""))
    async def _async_str(s):
        return s
    async def _nf(_n):
        return ""
    monkeypatch.setattr(pt, "_ensure_project_nc_folder", _nf)
    called = []
    async def _att(*a, **k):
        called.append(1)
        return "nope"
    monkeypatch.setattr(pt, "_attach_presence_to_project", _att)

    tok = pt.set_request_project_presence("pres-A", "ben")
    try:
        fn = pt.create_project
        coro = fn.coroutine if hasattr(fn, "coroutine") else fn
        out = await coro(name="Personal Only")
        assert "personal" in out.lower()
        assert not called
        insert = [c for c in conn.calls if "INSERT INTO projects" in c[0]]
        assert insert[0][1][0] == "pres-A"
    finally:
        pt.clear_request_project_presence(tok)
