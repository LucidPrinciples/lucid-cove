"""#D17 — the merchant (release role) gets READ-ONLY repo access, never push/PR.

Mercer's channel bound no repo tools, so he couldn't reach the code (it lives at /sites,
via git_*; NC is the brain). The fix binds a safe read subset universally for merchant
channels, mirroring the steward's universal queue/delegation modules — and must NOT leak
any write/dangerous dev tool.

#D40 adds scoped file read for NOTICE/LICENSE header audits.
"""
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

import src.graphs.channels as ch
from src.tools import dev_read_tools

DANGEROUS = {"git_push", "git_force_push", "create_github_pr", "git_commit", "git_add",
             "git_delete_branch", "db_execute", "git_create_branch", "git_revert_file"}


def _tool_names(tools):
    return {getattr(t, "name", str(t)) for t in tools}


def test_read_module_has_only_safe_read_tools():
    names = _tool_names(dev_read_tools.TOOLS)
    assert names == {"git_status", "git_diff", "git_log",
                     "git_current_branch", "git_diff_branch", "read_file"}
    assert not (names & DANGEROUS)


def _modules_for(mtype, cfg_tools):
    cfg = {"name": "mercer" if mtype == "merchant" else "stuart", "tools": cfg_tools}
    with patch.object(ch, "_is_manager_channel", return_value=True), \
         patch.object(ch, "_get_manager_config", return_value=(cfg, mtype)):
        return ch._channel_tool_modules("mercer-day" if mtype == "merchant" else "stuart-day")


def test_merchant_channel_gets_read_tools_universally():
    # even a cove.yaml that predates the fix (no dev_read in its list) gets it appended
    mods = _modules_for("merchant", ["tools.finance_tools"])
    assert "tools.dev_read_tools" in mods
    # and NOT the full dev set (no push/PR path for the merchant)
    assert "tools.dev_tools" not in mods


def test_merchant_no_duplicate_when_already_listed():
    mods = _modules_for("merchant", ["tools.finance_tools", "tools.dev_read_tools"])
    assert mods.count("tools.dev_read_tools") == 1


def test_steward_channel_does_not_get_merchant_read_module():
    # the steward already carries full dev_tools; the merchant read module is merchant-only
    mods = _modules_for("steward", ["tools.dev_tools"])
    assert "tools.dev_read_tools" not in mods
    # steward still gets its universal queue/delegation modules
    assert "tools.steward_queue_tools" in mods
    assert "tools.delegation_tools" in mods


# =============================================================================
# #D40 — read_file tool tests (scoped, read-only, @auto tier)
# =============================================================================

@pytest.mark.asyncio
async def test_read_file_rejects_path_traversal():
    result = await dev_read_tools.read_file("lucid-cove/../../../etc/passwd")
    assert "Error: Path traversal" in result


@pytest.mark.asyncio
async def test_read_file_rejects_absolute_path_escaping_repo():
    # Absolute path that would resolve outside any repo
    result = await dev_read_tools.read_file("/etc/passwd")
    assert "Error" in result  # Either not found or escapes root


@pytest.mark.asyncio
async def test_read_file_reads_valid_repo_file(tmp_path, monkeypatch):
    # Create a mock repo structure
    mock_repo = tmp_path / "mock-repo"
    mock_repo.mkdir()
    test_file = mock_repo / "test.txt"
    test_file.write_text("Hello, World!")

    # Patch _resolve_repo to return our tmp path
    monkeypatch.setattr(
        dev_read_tools, "_resolve_repo",
        lambda p: str(mock_repo) if p == "mock-repo" else str(tmp_path / p)
    )

    result = await dev_read_tools.read_file("mock-repo/test.txt")
    assert result == "Hello, World!"


@pytest.mark.asyncio
async def test_read_file_not_found():
    result = await dev_read_tools.read_file("nonexistent-repo/no-such-file.txt")
    assert "Error: File not found" in result


@pytest.mark.asyncio
async def test_read_file_respects_size_cap(tmp_path, monkeypatch):
    mock_repo = tmp_path / "mock-repo"
    mock_repo.mkdir()
    test_file = mock_repo / "large.txt"
    test_file.write_text("x" * 100_000)  # 100KB file

    monkeypatch.setattr(
        dev_read_tools, "_resolve_repo",
        lambda p: str(mock_repo) if p == "mock-repo" else str(tmp_path / p)
    )

    result = await dev_read_tools.read_file("mock-repo/large.txt", max_chars=50_000)
    assert len(result) <= 50_000 + 100  # Content + truncation marker
    assert "[...truncated" in result
    assert "total size: 100000 chars" in result


@pytest.mark.asyncio
async def test_read_file_no_gated_tools_in_module():
    """The module invariant: zero gated/write tools, only @auto read tools."""
    for tool in dev_read_tools.ALL_DEV_READ_TOOLS:
        # Check tool has auto approval tier (not notify/approve)
        from src.tools.approval import _get_tool_tier
        tier = _get_tool_tier(tool)
        assert tier == "auto", f"Tool {tool.name} has tier {tier}, expected 'auto'"
