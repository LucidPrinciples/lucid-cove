"""Memory provenance: source presence + multi-presence standing rule."""

from pathlib import Path

import pytest

from src.tools import memory_tools as mt


ROOT = Path(__file__).resolve().parents[1]


def test_migration_044_exists():
    sql = (ROOT / "docker/migrations/044_memory_source_presence.sql").read_text()
    assert "source_presence_id" in sql
    assert "source_operator_name" in sql


def test_manager_prompt_has_multi_presence_rules():
    src = (ROOT / "src/graphs/channels.py").read_text()
    assert "## Multi-Presence Coordination" in src
    assert "coordinate-and-report" in src
    assert "name who said" in src


def test_chat_binds_memory_presence():
    src = (ROOT / "src/dashboard/routes/chat.py").read_text()
    assert "set_request_memory_presence" in src
    assert "clear_request_memory_presence" in src


def test_enrich_prefixes_when_name_missing():
    assert mt._enrich_memory_content("approved the plan", "Jordan") == "Jordan: approved the plan"
    assert mt._enrich_memory_content("Jordan approved the plan", "Jordan") == "Jordan approved the plan"
    assert mt._enrich_memory_content("yes", None) == "yes"


@pytest.mark.asyncio
async def test_save_memory_stamps_presence(monkeypatch):
    captured = {}

    async def fake_store(**kwargs):
        captured.update(kwargs)
        return {"id": 42}

    monkeypatch.setattr(mt, "store_memory", fake_store)
    ptok = mt.set_request_memory_presence("pres-uuid-1", "Jordan")
    atok = mt.set_request_memory_agent("stuart")
    try:
        fn = mt.save_memory
        coro = fn.coroutine if hasattr(fn, "coroutine") else fn
        out = await coro(content="wants the warehouse plan first", category="fact", importance=0.7)
        assert "42" in out
        assert captured["source_presence_id"] == "pres-uuid-1"
        assert captured["source_operator_name"] == "Jordan"
        assert captured["content"].startswith("Jordan:")
        assert "from:jordan" in (captured.get("tags") or [])
    finally:
        mt.clear_request_memory_presence(ptok)
        mt.clear_request_memory_agent(atok)
