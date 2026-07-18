"""#PRJ1 — presence-scoped project/task agent tools (JF4 recipe)."""

import types
from pathlib import Path

import pytest

from src.tools import project_tools as pt


class _FakeResult:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows if rows is not None else ([] if row is None else [row])
        self.rowcount = len(self._rows)

    async def fetchone(self):
        return self._row

    async def fetchall(self):
        return list(self._rows)


class _FakeConn:
    def __init__(self):
        self.calls = []

    async def execute(self, sql, params=None):
        self.calls.append((sql, params))
        sql_l = " ".join(sql.lower().split())
        # create_project duplicate check
        if sql_l.startswith("select id from projects where slug"):
            return _FakeResult(row=None)
        # create_project insert
        if "insert into projects" in sql_l:
            return _FakeResult(row={"id": 42, "slug": params[1]})
        # create_task insert
        if "insert into tasks" in sql_l:
            return _FakeResult(row={"id": 7})
        # get_projects
        if "from projects p" in sql_l and "count(t.id)" in sql_l:
            return _FakeResult(rows=[])
        # get_tasks
        if "from tasks t" in sql_l:
            return _FakeResult(rows=[])
        # update/delete
        if sql_l.startswith("update tasks") or sql_l.startswith("delete from tasks"):
            return _FakeResult(row={"id": params[0] if params else 0, "title": "t"})
        return _FakeResult(row=None, rows=[])


class _DBCM:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *a):
        return False


def _patch_db(monkeypatch, conn):
    monkeypatch.setattr(pt, "get_db", lambda: _DBCM(conn))
    monkeypatch.setattr(pt, "_get_operator_id", lambda: "op")


def test_prj_scope_unbound_is_null():
    # ensure clean
    assert pt._acting_presence_id() is None
    sql, params = pt._prj_scope("presence_id")
    assert "IS NULL" in sql
    assert params == ()


def test_prj_scope_bound_equals_presence():
    tok = pt.set_request_project_presence("pres-A", "atlas")
    try:
        sql, params = pt._prj_scope("t.presence_id")
        assert "t.presence_id = %s" == sql
        assert params == ("pres-A",)
        assert pt._default_agent_name() == "atlas"
    finally:
        pt.clear_request_project_presence(tok)
    assert pt._acting_presence_id() is None
    assert pt._default_agent_name() == "stuart"


@pytest.mark.asyncio
async def test_create_project_writes_presence_id(monkeypatch):
    conn = _FakeConn()
    _patch_db(monkeypatch, conn)
    tok = pt.set_request_project_presence("pres-A", "atlas")
    try:
        # StructuredTool: call underlying coroutine via .ainvoke or coroutine
        fn = pt.create_project
        coro = fn.coroutine if hasattr(fn, "coroutine") else fn
        out = await coro(name="Book Promotion", description="promo", goals="")
        assert "book-promotion" in out.lower() or "Book Promotion" in out
        insert = [c for c in conn.calls if "INSERT INTO projects" in c[0]]
        assert insert, conn.calls
        sql, params = insert[0]
        assert "presence_id" in sql.lower()
        assert params[0] == "pres-A"
        assert params[1] == "book-promotion"
    finally:
        pt.clear_request_project_presence(tok)


@pytest.mark.asyncio
async def test_create_task_defaults_assignee_to_presence_agent(monkeypatch):
    conn = _FakeConn()
    _patch_db(monkeypatch, conn)
    tok = pt.set_request_project_presence("pres-A", "atlas")
    try:
        fn = pt.create_task
        coro = fn.coroutine if hasattr(fn, "coroutine") else fn
        out = await coro(title="Draft outline")
        assert "atlas" in out
        insert = [c for c in conn.calls if "INSERT INTO tasks" in c[0]]
        assert insert, conn.calls
        sql, params = insert[0]
        # (project_id, title, description, priority, assignee, created_by, source, expected_by, presence_id)
        assert params[4] == "atlas"
        assert params[5] == "atlas"
        assert params[8] == "pres-A"
    finally:
        pt.clear_request_project_presence(tok)


@pytest.mark.asyncio
async def test_create_task_unbound_defaults_stuart(monkeypatch):
    conn = _FakeConn()
    _patch_db(monkeypatch, conn)
    assert pt._acting_presence_id() is None
    fn = pt.create_task
    coro = fn.coroutine if hasattr(fn, "coroutine") else fn
    out = await coro(title="Cove task")
    assert "stuart" in out
    insert = [c for c in conn.calls if "INSERT INTO tasks" in c[0]]
    sql, params = insert[0]
    assert params[4] == "stuart"
    assert params[8] is None  # presence_id NULL = Cove board


@pytest.mark.asyncio
async def test_get_projects_scopes_sql(monkeypatch):
    conn = _FakeConn()
    _patch_db(monkeypatch, conn)
    tok = pt.set_request_project_presence("pres-B", "ben")
    try:
        fn = pt.get_projects
        coro = fn.coroutine if hasattr(fn, "coroutine") else fn
        await coro()
        sel = [c for c in conn.calls if "FROM projects p" in c[0]]
        assert sel
        sql, params = sel[0]
        assert "p.presence_id = %s" in sql
        assert params == ("pres-B",)
    finally:
        pt.clear_request_project_presence(tok)


@pytest.mark.asyncio
async def test_run_workflow_blocked_for_presence():
    tok = pt.set_request_project_presence("pres-A", "atlas")
    try:
        fn = pt.run_workflow
        coro = fn.coroutine if hasattr(fn, "coroutine") else fn
        out = await coro(task_id=1)
        assert "Cove-team" in out or "presence" in out.lower()
    finally:
        pt.clear_request_project_presence(tok)


def test_provision_presence_modules_include_project_tools():
    text = Path("/app/data/projects/lucid-cove/provision/centralized.py").read_text()
    # crude but stable: default modules list must mention project_tools near quick_list
    assert "tools.project_tools" in text
    assert "tools.quick_list_tools" in text


def test_chat_binds_and_clears_project_presence():
    text = Path("/app/data/projects/lucid-cove/src/dashboard/routes/chat.py").read_text()
    assert "set_request_project_presence" in text
    assert "clear_request_project_presence" in text
