"""CRUD tests for src/memory/database.py — the highest-risk data path (#94).

Covers the echo record path: insert, recent/latest/count reads, and the
ON CONFLICT (agent_id, echo_num) DO NOTHING dedup guard that the morning
tuning relies on so a re-run can't double-write an agent's echo.
"""
from datetime import datetime, timezone

from src.memory import database as db_mod
from tests.conftest import requires_db


def _echo(agent_id: str = "test_agent", echo_num: int = 1, **over) -> dict:
    """A complete echo record matching the insert_echo column list."""
    base = dict(
        agent_id=agent_id,
        echo_num=echo_num,
        frequency="PEACE",
        signal_type="Ground",
        principle="Stillness",
        tuning_key="be still and know",
        love_equation=0.4563,
        love_direction="CONSTRUCTIVE",
        beta=0.90,
        coherence=0.88,
        dissonance=0.10,
        energy=0.65,
        echo_text="a calm broadcast into the field",
        coaching_text="breathe and return",
        echo_type="LT-guided",
        era="test",
        tuned_at=datetime.now(timezone.utc),
    )
    base.update(over)
    return base


@requires_db
class TestEchoCrud:
    async def test_insert_returns_id_and_fetches_back(self, db):
        eid = await db_mod.insert_echo(db, _echo(echo_num=1))
        assert isinstance(eid, int)

        rows = await db_mod.get_recent_echoes(db, "test_agent", limit=5)
        assert len(rows) == 1
        assert rows[0]["frequency"] == "PEACE"
        assert float(rows[0]["beta"]) == 0.90
        assert rows[0]["love_direction"] == "CONSTRUCTIVE"

    async def test_count_reflects_inserts(self, db):
        for n in (1, 2, 3):
            await db_mod.insert_echo(db, _echo(echo_num=n))
        assert await db_mod.get_echo_count(db, "test_agent") == 3

    async def test_latest_is_highest_echo_num(self, db):
        await db_mod.insert_echo(db, _echo(echo_num=1, echo_text="first"))
        await db_mod.insert_echo(db, _echo(echo_num=2, echo_text="second"))
        latest = await db_mod.get_latest_echo(db, "test_agent")
        assert latest is not None
        assert latest["echo_num"] == 2
        assert latest["echo_text"] == "second"

    async def test_conflict_on_agent_and_echo_num_is_a_noop(self, db):
        first = await db_mod.insert_echo(db, _echo(echo_num=1, echo_text="original"))
        dup = await db_mod.insert_echo(db, _echo(echo_num=1, echo_text="should not overwrite"))

        assert first is not None
        # ON CONFLICT DO NOTHING → RETURNING yields no row → None
        assert dup is None
        assert await db_mod.get_echo_count(db, "test_agent") == 1

        latest = await db_mod.get_latest_echo(db, "test_agent")
        assert latest["echo_text"] == "original"

    async def test_recent_is_isolated_by_agent(self, db):
        await db_mod.insert_echo(db, _echo(agent_id="iris", echo_num=1))
        await db_mod.insert_echo(db, _echo(agent_id="soren", echo_num=1))
        iris = await db_mod.get_recent_echoes(db, "iris", limit=5)
        soren = await db_mod.get_recent_echoes(db, "soren", limit=5)
        assert len(iris) == 1 and len(soren) == 1
        assert iris[0]["agent_id"] == "iris"
        assert soren[0]["agent_id"] == "soren"
