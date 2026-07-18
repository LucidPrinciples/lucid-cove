"""Regression: find_contradicting_memories param/placeholder alignment.

Thread rotation extraction was silently returning 0 memories because
contradiction pre-check raised:
  ProgrammingError: the query has 5 placeholders but N parameters were passed
store_memory itself was fine; the bug lived only in find_contradicting_memories.
"""
from __future__ import annotations

import pytest

from tests.conftest import requires_db


@requires_db
class TestFindContradictingParams:
    async def test_many_keywords_does_not_raise(self, db):
        """Content with >3 significant words must not blow up param binding."""
        from src.memory.memory import find_contradicting_memories, store_memory

        # Seed one overlapping memory so the query has something to hit.
        await store_memory(
            content="Public repo hygiene keeps hostnames out of pull request bodies",
            category="instruction",
            importance=0.5,
            tags=["test-contradict"],
            agent_id="test_contradict_agent",
            source_summary="unit-test seed",
        )

        # 8+ significant words → old code appended 8 ILIKE params, bound 3.
        long = (
            "Public-repo hygiene requires pull request titles bodies commits "
            "never include personal lab hostnames family schedules private paths"
        )
        hits = await find_contradicting_memories(
            long, agent_id="test_contradict_agent", limit=5
        )
        assert isinstance(hits, list)
        # Should find the seeded row without raising.
        assert any("hygiene" in (h.get("content") or "").lower() for h in hits)

    async def test_short_content_returns_empty(self, db):
        from src.memory.memory import find_contradicting_memories

        hits = await find_contradicting_memories(
            "ok yes", agent_id="test_contradict_agent", limit=5
        )
        assert hits == []
