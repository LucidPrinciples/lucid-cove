"""Backlog board tools — steward cross-scope access to the operator's intake board.

07-11 origin: Stuart, asked about #D52, searched his OWN NC scope and honestly found
nothing — the ticket sat on the operator's board (jag's jules-backlog.md). These tools
reach the board through the INTAKE OWNER'S credentials and connect intake (board) to
execution (steward_queue). Board line ops are pure functions, tested directly.
"""
import pytest

import src.tools.backlog_tools as bt

BOARD = """# jules backlog — Clearfield (DEV lane)

> intro text

## Now

- [ ] **#D52 Effect-verification for remote git tools**. Verify remote state. `[dev]`
- [ ] **#D50 Telemetry reconciliation**. Cards vs GitHub.

## Soon

- [ ] **#1626 Set-Address agent pop-in**. Copy approved.

## Completed

- [x] **#D41 Ground agents in real repos**. Merged PR #63.
"""


# =============================================================================
# Pure helpers
# =============================================================================

def test_find_ticket_returns_line_and_lane():
    idx, lane = bt.find_ticket(BOARD, "#D52")
    assert idx is not None and lane == "Now"
    idx2, lane2 = bt.find_ticket(BOARD, "1626")   # missing '#' normalized
    assert idx2 is not None and lane2 == "Soon"


def test_find_ticket_whole_token_only():
    # '#D5' must not match '#D52' / '#D50'
    idx, _ = bt.find_ticket(BOARD, "#D5")
    assert idx is None


def test_move_ticket_lane():
    text, msg = bt.move_ticket_lane(BOARD, "#D52", "soon")
    assert "Moved #D52" in msg and "Now → Soon" in msg
    idx, lane = bt.find_ticket(text, "#D52")
    assert lane == "Soon"
    # nothing lost
    assert text.count("#D50") == 1 and text.count("#1626") == 1


def test_move_to_unknown_lane_lists_lanes():
    text, msg = bt.move_ticket_lane(BOARD, "#D52", "someday")
    assert text == BOARD and "not found" in msg and "Now" in msg


def test_annotate_and_done():
    text, msg = bt.annotate_ticket(BOARD, "#D50", "→ queue#7")
    assert "queue#7" in text and "Annotated" in msg
    text2, msg2 = bt.mark_ticket_done(text, "#D50")
    idx, _ = bt.find_ticket(text2, "#D50")
    assert text2.split("\n")[idx].strip().startswith("- [x]")
    # already-done stays put
    _, msg3 = bt.mark_ticket_done(text2, "#D41")
    assert "already" in msg3


def test_ticket_title():
    t = bt.ticket_title(BOARD, "#D52")
    assert t.startswith("#D52 Effect-verification") and len(t) <= 70


# =============================================================================
# Tools (board I/O + queue mocked)
# =============================================================================

class _Saved:
    text = None


def _wire(monkeypatch, board=BOARD, qid=7, fail_put=False):
    saved = _Saved()

    async def fake_get():
        return board, "knight's board (jag:AgentSkills/Ops/jules-backlog.md)", 'W/"etag-1"'

    async def fake_put(text, etag=""):
        if fail_put:
            raise RuntimeError("Board write failed (HTTP 507)")
        saved.etag = etag
        saved.text = text

    async def fake_insert(source, title, assignee):
        saved.queue = (source, title, assignee)
        return qid

    monkeypatch.setattr(bt, "_board_get", fake_get)
    monkeypatch.setattr(bt, "_board_put", fake_put)
    monkeypatch.setattr(bt, "_insert_queue_row", fake_insert)
    return saved


@pytest.mark.asyncio
async def test_backlog_board_reads_with_provenance(monkeypatch):
    _wire(monkeypatch)
    out = await bt.backlog_board.coroutine()
    assert out.startswith("SOURCE: knight's board")
    assert "#D52" in out


@pytest.mark.asyncio
async def test_backlog_board_lane_filter(monkeypatch):
    _wire(monkeypatch)
    out = await bt.backlog_board.coroutine("soon")
    assert "#1626" in out and "#D52" not in out


@pytest.mark.asyncio
async def test_backlog_pull_creates_queue_row_and_annotates(monkeypatch):
    saved = _wire(monkeypatch)
    out = await bt.backlog_pull.coroutine("#D52", "stuart-clearfield")
    assert "queue" in out and "[7]" in out and "assigned to stuart-clearfield" in out
    assert saved.queue[0] == "board:#D52"
    assert "→ queue#7" in saved.text  # board line annotated


@pytest.mark.asyncio
async def test_backlog_pull_missing_ticket(monkeypatch):
    saved = _wire(monkeypatch)
    out = await bt.backlog_pull.coroutine("#D99")
    assert "not found" in out and saved.text is None  # board untouched


@pytest.mark.asyncio
async def test_backlog_update_move_note_done(monkeypatch):
    saved = _wire(monkeypatch)
    out = await bt.backlog_update.coroutine("#D50", lane="completed",
                                            note="shipped", done=True)
    assert "Moved #D50" in out and "Marked #D50 done" in out
    idx, lane = bt.find_ticket(saved.text, "#D50")
    assert lane == "Completed"
    assert "shipped" in saved.text.split("\n")[idx]


@pytest.mark.asyncio
async def test_backlog_update_write_failure_is_loud(monkeypatch):
    _wire(monkeypatch, fail_put=True)
    out = await bt.backlog_update.coroutine("#D50", note="x")
    assert "nothing saved" in out


@pytest.mark.asyncio
async def test_backlog_update_requires_an_action(monkeypatch):
    _wire(monkeypatch)
    out = await bt.backlog_update.coroutine("#D50")
    assert "Nothing to do" in out


# =============================================================================
# Wiring: steward channel gets the module universally; no leak to merchant
# =============================================================================

def test_steward_channel_gets_backlog_tools_universally():
    from unittest.mock import patch
    import src.graphs.channels as ch
    cfg = {"name": "stuart", "tools": ["tools.dev_tools"]}
    with patch.object(ch, "_is_manager_channel", return_value=True), \
         patch.object(ch, "_get_manager_config", return_value=(cfg, "steward")):
        mods = ch._channel_tool_modules("stuart-day")
    assert "tools.backlog_tools" in mods

    cfg2 = {"name": "mercer", "tools": ["tools.finance_tools"]}
    with patch.object(ch, "_is_manager_channel", return_value=True), \
         patch.object(ch, "_get_manager_config", return_value=(cfg2, "merchant")):
        mods2 = ch._channel_tool_modules("mercer-day")
    assert "tools.backlog_tools" not in mods2


def test_steward_prompt_includes_intake_geography():
    from src.agents.identity import _dev_workflow_block
    block = _dev_workflow_block({"archetype": "steward", "name": "stuart",
                                 "can_delegate_to": ["mercer"]})
    assert "backlog_board" in block and "backlog_pull" in block
    member = _dev_workflow_block({"archetype": "companion", "name": "atlas"})
    assert "backlog_pull" not in member


# =============================================================================
# OPS-5b — conditional writes (compare-and-swap): stale writers cannot stomp
# =============================================================================

@pytest.mark.asyncio
async def test_writes_are_conditional_on_the_read_etag(monkeypatch):
    saved = _wire(monkeypatch)
    await bt.backlog_update.coroutine("#D50", note="x")
    assert saved.etag == 'W/"etag-1"'  # the PUT carried the read's etag


@pytest.mark.asyncio
async def test_stale_write_rereads_and_reapplies(monkeypatch):
    """First PUT hits 412 (board changed under us) → the edit re-applies to the
    FRESH board content and the second PUT succeeds."""
    saved = _Saved()
    calls = {"get": 0, "put": 0}
    fresh_board = BOARD.replace("Cards vs GitHub.", "Cards vs GitHub (fresh).")

    async def fake_get():
        calls["get"] += 1
        text = BOARD if calls["get"] == 1 else fresh_board
        return text, "b", f'W/"etag-{calls["get"]}"'

    async def fake_put(text, etag=""):
        calls["put"] += 1
        if calls["put"] == 1:
            raise bt.BoardStale("etag mismatch")
        saved.text, saved.etag = text, etag

    monkeypatch.setattr(bt, "_board_get", fake_get)
    monkeypatch.setattr(bt, "_board_put", fake_put)

    out = await bt.backlog_update.coroutine("#D50", note="retry-note")
    assert "retry-note" in out
    assert "(fresh)" in saved.text          # edit landed on the FRESH content
    assert saved.etag == 'W/"etag-2"'       # conditional on the re-read
    assert calls["get"] == 2 and calls["put"] == 2


@pytest.mark.asyncio
async def test_stale_twice_reports_honestly_never_forces(monkeypatch):
    async def fake_get():
        return BOARD, "b", 'W/"e"'

    async def fake_put(text, etag=""):
        raise bt.BoardStale("etag mismatch")

    monkeypatch.setattr(bt, "_board_get", fake_get)
    monkeypatch.setattr(bt, "_board_put", fake_put)

    out = await bt.backlog_update.coroutine("#D50", note="x")
    assert "NOT SAVED" in out and "concurrent writer" in out
