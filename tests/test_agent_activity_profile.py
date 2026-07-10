# #D22 — Team-page per-agent activity profile. A read-only feed assembled from
# EXISTING tables (tasks, steward_queue, chat_threads, echoes, delegated tasks) —
# legibility for the build team without giving every agent a chat tab.
import contextlib
import pathlib

import pytest

import src.dashboard.routes.agents as agents
from src.dashboard.routes.agents import _delegation_phase, _extract_report_back

ROOT = pathlib.Path(__file__).resolve().parents[1]


# ── pure helpers ─────────────────────────────────────────────────────────────
def test_delegation_phase_mapping():
    assert _delegation_phase("done") == "replied"
    assert _delegation_phase("blocked") == "failed"
    assert _delegation_phase("in_progress") == "dispatched"
    assert _delegation_phase("pending") == "dispatched"
    assert _delegation_phase("") == "dispatched"


def test_extract_report_back():
    notes = "created\n[archimedes reply] Built the guard, PR opened."
    assert _extract_report_back(notes) == "Built the guard, PR opened."
    # takes the LAST report when several are appended
    multi = "[a reply] first\n[a reply] second and final"
    assert _extract_report_back(multi) == "second and final"
    assert _extract_report_back("") == ""
    assert _extract_report_back("no marker here") == ""


# ── endpoint against a scripted fake DB ──────────────────────────────────────
class _Result:
    def __init__(self, rows):
        # a dict = a single-row (fetchone) result; a list = a fetchall result
        if isinstance(rows, dict):
            self._rows = [rows]
        elif rows is None:
            self._rows = []
        else:
            self._rows = list(rows)

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def fetchall(self):
        return self._rows


class _Conn:
    """Returns the scripted row-list for each execute() in order."""
    def __init__(self, script):
        self._script = list(script)
        self.sql = []

    async def execute(self, sql, params=None):
        self.sql.append(sql)
        return _Result(self._script.pop(0) if self._script else [])


def _patch_db(monkeypatch, conn):
    @contextlib.asynccontextmanager
    async def _cm():
        yield conn
    monkeypatch.setattr("src.memory.database.get_db", lambda: _cm())


@pytest.mark.asyncio
async def test_activity_assembles_all_sections(monkeypatch):
    # order of queries: tasks, queue, chat_threads(last turn), echoes, delegations
    conn = _Conn([
        [{"id": 1, "title": "Fix X", "status": "in_progress", "priority": "normal", "updated_at": None}],
        [{"id": 7, "source": "#D15", "title": "Build guard", "status": "assigned", "pr_url": "", "updated_at": None}],
        {"last_message_at": None, "message_count": 39},          # fetchone → single row
        {"frequency": "Clarity", "love_equation": 0.82, "echo_num": 6, "tuned_at": None},
        [{"id": 9, "title": "Do the thing", "status": "done", "notes": "[archimedes reply] done!", "created_at": None}],
    ])
    _patch_db(monkeypatch, conn)
    out = await agents.get_agent_activity("archimedes")
    assert out["agent_id"] == "archimedes"
    assert out["tasks"][0]["title"] == "Fix X"
    assert out["queue"][0]["source"] == "#D15"
    assert out["last_turn"]["steps"] == 39
    assert out["echo_today"]["frequency"] == "Clarity"
    d = out["delegations"][0]
    assert d["phase"] == "replied" and d["report_back"] == "done!"


@pytest.mark.asyncio
async def test_activity_sections_are_independent(monkeypatch):
    # empty tables everywhere → a valid, empty feed (never blanks / never raises)
    conn = _Conn([[], [], None, None, []])
    _patch_db(monkeypatch, conn)
    out = await agents.get_agent_activity("gabe")
    assert out["tasks"] == [] and out["queue"] == []
    assert out["last_turn"] is None and out["echo_today"] is None
    assert out["delegations"] == []


@pytest.mark.asyncio
async def test_activity_queries_scope_to_open_work(monkeypatch):
    conn = _Conn([[], [], None, None, []])
    _patch_db(monkeypatch, conn)
    await agents.get_agent_activity("vera")
    joined = " ".join(conn.sql)
    assert "FROM tasks" in joined and "status IN ('pending','in_progress')" in joined
    assert "FROM steward_queue" in joined and "status IN ('assigned','in_review')" in joined
    assert "source = 'agent'" in joined  # delegations only


# ── delegation parts already required by #D22 (regression) ───────────────────
def test_delegation_keeps_message_count_and_prints():
    src = (ROOT / "src" / "tools" / "delegation_tools.py").read_text()
    # message_count stays honest for background delegated turns
    assert "update_thread_stats(thread_id, message_count=len(msgs))" in src
    # logging goes through print() so turns show in docker logs
    assert 'print(f"{ts_log()} [delegation]' in src


# ── UI wiring ────────────────────────────────────────────────────────────────
def test_team_panel_has_activity_card():
    panels = (ROOT / "src" / "dashboard" / "static" / "js" / "panels.js").read_text()
    assert 'id="agp-activity"' in panels


def test_overview_loads_and_renders_activity():
    ov = (ROOT / "src" / "dashboard" / "static" / "js" / "overview.js").read_text()
    assert "loadAgentActivity(agentId)" in ov
    assert "async function loadAgentActivity(" in ov
    assert "/activity" in ov
