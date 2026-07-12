"""Tests for #D47: queue close-out syncs the board.

When a queue_update sets status='done', the matching board ticket must be:
- moved to COMPLETED lane
- marked done (checkbox flipped)
- annotated with the PR URL
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from src.tools.steward_queue_tools import _sync_board_on_done, _update


@pytest.fixture
def mock_board_tools():
    """Patch the backlog_tools functions used by _sync_board_on_done."""
    async def _fake_cas(apply_fn, attempts=2):
        new_text, msgs = apply_fn("board text")
        return msgs, "test-board", True

    with patch("src.tools.backlog_tools._cas_edit", side_effect=_fake_cas) as get, \
         patch("src.tools.backlog_tools._board_put") as put, \
         patch("src.tools.backlog_tools.move_ticket_lane") as move, \
         patch("src.tools.backlog_tools.mark_ticket_done") as mark, \
         patch("src.tools.backlog_tools.annotate_ticket") as ann:
        move.return_value = ("moved text", "moved")
        mark.return_value = ("marked text", "marked")
        ann.return_value = ("annotated text", "annotated")
        yield {"get": get, "put": put, "move": move, "mark": mark, "ann": ann}


@pytest.mark.asyncio
async def test_sync_board_on_done_moves_to_completed(mock_board_tools):
    await _sync_board_on_done("board:#D52", "https://github.com/org/repo/pull/65")
    mock_board_tools["move"].assert_called_once()
    args = mock_board_tools["move"].call_args[0]
    assert args[1] == "#D52"
    assert args[2] == "completed"


@pytest.mark.asyncio
async def test_sync_board_on_done_marks_done(mock_board_tools):
    await _sync_board_on_done("board:#D52", "https://github.com/org/repo/pull/65")
    mock_board_tools["mark"].assert_called_once()


@pytest.mark.asyncio
async def test_sync_board_on_done_annotates_pr_url(mock_board_tools):
    await _sync_board_on_done("board:#D52", "https://github.com/org/repo/pull/65")
    mock_board_tools["ann"].assert_called_once()
    args = mock_board_tools["ann"].call_args[0]
    assert "merged" in args[2]
    assert "https://github.com/org/repo/pull/65" in args[2]


@pytest.mark.asyncio
async def test_sync_board_on_done_skips_non_board_source(mock_board_tools):
    """Internal queue items (not from board) don't sync."""
    await _sync_board_on_done("internal", "")
    mock_board_tools["get"].assert_not_called()


@pytest.mark.asyncio
async def test_sync_board_on_done_writes_board(mock_board_tools):
    """The write goes through the CAS seam (conditional If-Match, OPS-5b)."""
    await _sync_board_on_done("board:#D52", "https://github.com/org/repo/pull/65")
    mock_board_tools["get"].assert_called_once()  # "get" now = the _cas_edit seam


@pytest.mark.asyncio
async def test_sync_board_on_done_logs_failure_no_raise(caplog):
    """Board sync failures (incl. persistent stale) are logged, not raised."""
    from unittest.mock import patch as _patch

    async def _boom(apply_fn, attempts=2):
        raise Exception("WebDAV 500")

    with _patch("src.tools.backlog_tools._cas_edit", side_effect=_boom):
        await _sync_board_on_done("board:#D52", "")
    assert "Board sync failed" in caplog.text
    assert "WebDAV 500" in caplog.text


@pytest.mark.asyncio
async def test_sync_board_on_done_no_pr_url_skips_annotation(mock_board_tools):
    await _sync_board_on_done("board:#D52", "")
    mock_board_tools["ann"].assert_not_called()
    mock_board_tools["move"].assert_called_once()
    mock_board_tools["mark"].assert_called_once()
