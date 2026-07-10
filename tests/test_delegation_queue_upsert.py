# #D37 — delegate_task must make every delegation queue-visible from birth.
# The old link was UPDATE-only and silently no-op'd when the ticket never flowed
# through the board's → Team button (no steward_queue row existed), leaving the
# queue blind to delegated work. link_or_create_queue_row now UPSERTs.
#
# No DB harness in-repo, so exercise the logic against a fake connection that
# records the SQL it's asked to run and returns scripted rows.
import pathlib

import pytest

from src.tools.delegation_tools import link_or_create_queue_row

DELEG = pathlib.Path(__file__).resolve().parents[1] / "src" / "tools" / "delegation_tools.py"


class _FakeResult:
    def __init__(self, row):
        self._row = row

    async def fetchone(self):
        return self._row


class _FakeConn:
    """Returns the scripted rows in order, one per execute()."""
    def __init__(self, rows):
        self._rows = list(rows)
        self.calls = []

    async def execute(self, sql, params=None):
        self.calls.append((sql, params))
        return _FakeResult(self._rows.pop(0) if self._rows else None)


@pytest.mark.asyncio
async def test_empty_ref_touches_nothing():
    conn = _FakeConn([])
    note = await link_or_create_queue_row(conn, "", "archimedes", "some brief")
    assert note == ""
    assert conn.calls == []  # no ref → no queue write at all


@pytest.mark.asyncio
async def test_existing_open_row_is_claimed_not_duplicated():
    # UPDATE hits an existing row → single statement, no INSERT.
    conn = _FakeConn([{"id": 5}])
    note = await link_or_create_queue_row(conn, "#D15", "archimedes", "brief here")
    assert "[5]" in note and "assigned" in note
    assert len(conn.calls) == 1
    assert "UPDATE steward_queue" in conn.calls[0][0]


@pytest.mark.asyncio
async def test_missing_row_is_created():
    # UPDATE returns nothing (no open row) → INSERT a fresh assigned row.
    conn = _FakeConn([None, {"id": 9}])
    note = await link_or_create_queue_row(
        conn, "#D15", "archimedes", "Build the pre-queue validation guard.")
    assert "[9]" in note and "created" in note
    assert len(conn.calls) == 2
    insert_sql, insert_params = conn.calls[1]
    assert "INSERT INTO steward_queue" in insert_sql
    # source = ref, status assigned, assignee = target agent
    assert insert_params[0] == "#D15"
    assert insert_params[2] == "archimedes"
    assert "'assigned'" in insert_sql
    # title is derived from the brief, capped at 70 chars
    assert insert_params[1] == "Build the pre-queue validation guard."


@pytest.mark.asyncio
async def test_created_title_is_capped_and_falls_back_to_ref():
    conn = _FakeConn([None, {"id": 1}])
    await link_or_create_queue_row(conn, "#D99", "gabe", "x" * 200)
    assert len(conn.calls[1][1][1]) == 70  # brief[:70]

    conn2 = _FakeConn([None, {"id": 2}])
    await link_or_create_queue_row(conn2, "#D99", "gabe", "   ")
    assert conn2.calls[1][1][1] == "#D99"  # empty brief → ref as title


def test_delegate_task_isolates_queue_link_in_its_own_transaction():
    # The task insert must not be roll-back-able by a queue failure: the queue
    # upsert runs in a SEPARATE get_db() block, wrapped best-effort.
    src = DELEG.read_text()
    assert "link_or_create_queue_row" in src
    assert src.count("async with get_db() as conn:") >= 2
