"""#D54 — Semantic memory index over vault archive + memory_search/memory_get tools."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.memory.archive_index import (
    ARCHIVE_FILE_PREFIX,
    ARCHIVE_SESSION_PREFIX,
    extract_session_entries,
    get_archive_chunk,
    list_dated_session_files,
    resolve_vault_lp,
    search_memory_unified,
    session_log_archive_path,
)
from src.tools import memory_tools as mt


SAMPLE_ARCHIVE = """# Session Log Archive

### 2026-07-10 (session 12) — Haven nesting and registry

Woods could not nest Mann because Mann never registered with the hub.
Task #22 tracks ensure-space POST from Cove Admin.

### 2026-07-11 (session 13) — Drop identity tuning dedup

Replaced calendar-day dedup with Drop identity (frequency, principle, tuning_key).
Triple overnight Courage echoes on Mann should stop after deploy.
"""


def test_extract_session_entries_parses_headers_and_bodies():
    entries = extract_session_entries(SAMPLE_ARCHIVE)
    assert len(entries) == 2
    assert entries[0]["session_num"] == 12
    assert entries[0]["date"] == "2026-07-10"
    assert "Haven" in entries[0]["title"]
    assert "ensure-space" in entries[0]["content"]
    assert entries[1]["session_num"] == 13
    assert "Drop identity" in entries[1]["content"]


def test_extract_accepts_ascii_hyphen_dash():
    text = "### 2026-01-01 (session 1) - First session\n\nBody here.\n"
    entries = extract_session_entries(text)
    assert len(entries) == 1
    assert entries[0]["session_num"] == 1
    assert entries[0]["title"] == "First session"


def test_resolve_vault_lp_prefers_lp_vault_subdir(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    lp = vault / "LP-Vault"
    (lp / "Archive").mkdir(parents=True)
    (lp / "Memory.md").write_text("# mem\n", encoding="utf-8")
    monkeypatch.setenv("VAULT_DIR", str(vault))
    # reload env() reads os.environ via registry default override path —
    # session_log uses env("VAULT_DIR")
    assert resolve_vault_lp() == lp
    assert session_log_archive_path() == lp / "Archive" / "session-log-archive.md"


def test_resolve_vault_lp_when_archive_at_vault_root(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    (vault / "Archive").mkdir(parents=True)
    (vault / "Memory.md").write_text("# mem\n", encoding="utf-8")
    monkeypatch.setenv("VAULT_DIR", str(vault))
    assert resolve_vault_lp() == vault


def test_list_dated_session_files_finds_session_named_md(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    arch = vault / "LP-Vault" / "Archive"
    arch.mkdir(parents=True)
    (arch / "session-log-archive.md").write_text(SAMPLE_ARCHIVE, encoding="utf-8")
    (arch / "session-log-20260709.md").write_text("dated notes", encoding="utf-8")
    (arch / "2026-07-08.md").write_text("day file", encoding="utf-8")
    (arch / "README.md").write_text("skip", encoding="utf-8")
    monkeypatch.setenv("VAULT_DIR", str(vault))
    files = list_dated_session_files()
    names = {p.name for p in files}
    assert "session-log-20260709.md" in names
    assert "2026-07-08.md" in names
    assert "session-log-archive.md" not in names
    assert "README.md" not in names


def test_archive_prefixes_stable():
    assert ARCHIVE_SESSION_PREFIX == "_archive_s"
    assert ARCHIVE_FILE_PREFIX.startswith("_archive_file:")


@pytest.mark.asyncio
async def test_search_memory_unified_merges_sources(monkeypatch):
    async def fake_mems(**kwargs):
        return [{
            "id": 7,
            "content": "Haven nest needs ensure-space",
            "category": "technical",
            "importance": 0.8,
            "tags": ["haven"],
            "similarity": 0.9,
            "score": 0.88,
        }]

    async def fake_arch(query, limit=8, min_similarity=0.28):
        return [{
            "ref": "archive:_archive_s12#0",
            "source": "archive",
            "text": "Mann never registered with the hub",
            "similarity": 0.7,
            "title": "Haven nesting",
            "path": "Archive/session-log-archive.md",
            "session_num": "12",
            "session_date": "2026-07-10",
            "doc_name": "_archive_s12",
            "chunk_index": 0,
        }]

    monkeypatch.setattr(
        "src.memory.memory.search_memories_semantic",
        fake_mems,
    )
    monkeypatch.setattr(
        "src.memory.archive_index.search_archive_semantic",
        fake_arch,
    )

    results = await search_memory_unified(
        "Haven nesting", limit=5, source="all", agent_id="test-agent"
    )
    assert len(results) == 2
    assert results[0]["source"] == "memory"  # higher score
    assert results[0]["ref"] == "memory:7"
    assert results[1]["source"] == "archive"


@pytest.mark.asyncio
async def test_memory_search_tool_formats_hits(monkeypatch):
    async def fake_unified(**kwargs):
        return [
            {
                "ref": "memory:3",
                "source": "memory",
                "content": "Drop identity dedup shipped",
                "category": "decision",
                "tags": ["tuning"],
                "similarity": 0.81,
                "score": 0.81,
            },
            {
                "ref": "archive:_archive_s13#0",
                "source": "archive",
                "content": "Triple overnight Courage echoes",
                "similarity": 0.6,
                "title": "Drop identity",
                "path": "Archive/session-log-archive.md",
                "session_num": "13",
            },
        ]

    monkeypatch.setattr(
        "src.memory.archive_index.search_memory_unified",
        fake_unified,
    )
    out = await mt.memory_search.ainvoke({"query": "tuning dedup", "source": "all"})
    assert "memory:3" in out
    assert "archive:_archive_s13#0" in out
    assert "memory_get" in out


@pytest.mark.asyncio
async def test_memory_get_memory_ref(monkeypatch):
    async def fake_get(mid, agent_id=None):
        assert mid == 42
        return {
            "id": 42,
            "content": "RB16 seed into cove-core",
            "category": "technical",
            "importance": 0.7,
            "tags": ["runbook"],
            "is_active": True,
            "created_at": "2026-07-15",
            "source_channel": "day",
            "source_summary": "ops",
        }

    monkeypatch.setattr(mt, "get_memory_service", fake_get)
    out = await mt.memory_get.ainvoke({"ref": "memory:42"})
    assert "RB16" in out
    assert "memory:42" in out


@pytest.mark.asyncio
async def test_memory_get_bare_id(monkeypatch):
    async def fake_get(mid, agent_id=None):
        return {
            "id": mid,
            "content": "bare id works",
            "category": "general",
            "importance": 0.5,
            "tags": [],
            "is_active": True,
        }

    monkeypatch.setattr(mt, "get_memory_service", fake_get)
    out = await mt.memory_get.ainvoke({"ref": "99"})
    assert "bare id works" in out


@pytest.mark.asyncio
async def test_memory_get_archive_ref(monkeypatch):
    async def fake_chunk(ref):
        assert ref == "archive:_archive_s12#0"
        return {
            "ref": ref,
            "text": "full archive passage about Mann registry",
            "path": "Archive/session-log-archive.md",
            "session_num": "12",
            "session_date": "2026-07-10",
            "session_title": "Haven nesting",
            "metadata": {},
        }

    monkeypatch.setattr(
        "src.memory.archive_index.get_archive_chunk",
        fake_chunk,
    )
    out = await mt.memory_get.ainvoke({"ref": "archive:_archive_s12#0"})
    assert "Mann registry" in out
    assert "Haven nesting" in out


def test_memory_tools_registry_includes_semantic():
    names = {t.name for t in mt.ALL_MEMORY_TOOLS}
    assert "memory_search" in names
    assert "memory_get" in names
    assert "search_memory" in names  # keyword path still present
