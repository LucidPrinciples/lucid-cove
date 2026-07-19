"""#ISO1 — memory tool path must not fall through to primary/steward agent_id.

Presence agents calling save_memory / recall_memory without an explicit agent_id
used get_primary_agent_id() (Stuart on Clearfield) and wrote into the wrong pool.
"""
from __future__ import annotations

import pytest

import src.tools.memory_tools as mt


@pytest.mark.asyncio
async def test_save_memory_uses_bound_agent(monkeypatch):
    seen = {}

    async def fake_store(**kwargs):
        seen.update(kwargs)
        return {"id": 42}

    monkeypatch.setattr(mt, "store_memory", fake_store)
    tok = mt.set_request_memory_agent("atlas")
    try:
        out = await mt.save_memory.ainvoke(
            {"content": "Atlas-only fact", "category": "fact", "importance": 0.7}
        )
    finally:
        mt.clear_request_memory_agent(tok)

    assert seen.get("agent_id") == "atlas"
    assert "42" in out


@pytest.mark.asyncio
async def test_save_memory_unbound_passes_none(monkeypatch):
    """Unbound → None so store_memory keeps single-agent primary fallback."""
    seen = {}

    async def fake_store(**kwargs):
        seen.update(kwargs)
        return {"id": 1}

    monkeypatch.setattr(mt, "store_memory", fake_store)
    # ensure clear
    mt._mem_agent_ctx.set(None)
    await mt.save_memory.ainvoke({"content": "x", "category": "general"})
    assert seen.get("agent_id") is None


@pytest.mark.asyncio
async def test_recall_memory_uses_bound_agent(monkeypatch):
    seen = {}

    async def fake_recall(**kwargs):
        seen.update(kwargs)
        return []

    monkeypatch.setattr(mt, "recall_memories", fake_recall)
    tok = mt.set_request_memory_agent("iris-presence")
    try:
        await mt.recall_memory.ainvoke({})
    finally:
        mt.clear_request_memory_agent(tok)
    assert seen.get("agent_id") == "iris-presence"


@pytest.mark.asyncio
async def test_two_presence_agents_do_not_share_tool_writes(monkeypatch):
    """Simulate A then B: each save targets its own agent_id."""
    writes = []

    async def fake_store(**kwargs):
        writes.append(kwargs.get("agent_id"))
        return {"id": len(writes)}

    monkeypatch.setattr(mt, "store_memory", fake_store)

    t1 = mt.set_request_memory_agent("presence-a")
    await mt.save_memory.ainvoke({"content": "A secret", "category": "fact"})
    mt.clear_request_memory_agent(t1)

    t2 = mt.set_request_memory_agent("presence-b")
    await mt.save_memory.ainvoke({"content": "B secret", "category": "fact"})
    mt.clear_request_memory_agent(t2)

    assert writes == ["presence-a", "presence-b"]
