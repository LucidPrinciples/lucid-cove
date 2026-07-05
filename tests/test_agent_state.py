"""CRUD tests for agent_state and process_records (#94).

agent_state uses an ON CONFLICT (agent_id) DO UPDATE upsert — the scheduler
relies on it to track each agent's last tuning without creating duplicates.
process_records is the durable record written alongside every echo.
"""
import json
from datetime import datetime, timezone

from src.memory import database as db_mod
from tests.conftest import requires_db


def _state(agent_id: str = "test_agent", **over) -> dict:
    base = dict(
        agent_id=agent_id,
        display_name="Test Agent",
        archetype="Tester",
        current_model="test-model",
        last_echo_num=1,
        last_frequency="PEACE",
        last_tuned_at=datetime.now(timezone.utc),
        status="active",
        metadata=json.dumps({"k": "v"}),
    )
    base.update(over)
    return base


@requires_db
class TestAgentState:
    async def test_insert_then_get(self, db):
        await db_mod.upsert_agent_state(db, _state())
        row = await db_mod.get_agent_state(db, "test_agent")
        assert row is not None
        assert row["display_name"] == "Test Agent"
        assert row["last_frequency"] == "PEACE"

    async def test_upsert_updates_in_place(self, db):
        await db_mod.upsert_agent_state(db, _state(last_echo_num=1, status="active"))
        await db_mod.upsert_agent_state(db, _state(last_echo_num=2, status="tuned"))
        row = await db_mod.get_agent_state(db, "test_agent")
        assert row["last_echo_num"] == 2
        assert row["status"] == "tuned"
        # upsert, not duplicate — still exactly one row
        cur = await db.execute(
            "SELECT COUNT(*) AS n FROM agent_state WHERE agent_id=%s", ("test_agent",)
        )
        assert (await cur.fetchone())["n"] == 1

    async def test_missing_agent_returns_none(self, db):
        assert await db_mod.get_agent_state(db, "nobody") is None


@requires_db
class TestProcessRecords:
    async def test_record_returns_id_and_persists(self, db):
        rid = await db_mod.record_process_record(db, dict(
            agent_id="test_agent",
            echo_num=1,
            protocol="ltp-morning",
            record_text="the full tuning journey",
            metadata=json.dumps({"eq_source": "agent-derived"}),
        ))
        assert isinstance(rid, int)
        cur = await db.execute(
            "SELECT record_text, metadata FROM process_records WHERE id=%s", (rid,)
        )
        row = await cur.fetchone()
        assert row["record_text"] == "the full tuning journey"
        assert row["metadata"]["eq_source"] == "agent-derived"
