"""Ops-Visibility Surface — reconcile() is the product, tested hardest.

reconcile() diffs three owned-state sources (intake board, steward queue, GitHub
REST) and returns the DISAGREEMENTS — the fabrication-class guard (#D18/#D52/#D50).
Also covers: board parsing (#D43 every lane its own group), ticket-id matching,
the route assembly with fake sources, and the admin/operator scope gate.
"""
import pytest

import src.dashboard.routes.ops_visibility as ov


# =============================================================================
# Ticket-id matching (pure)
# =============================================================================

def test_ids_in_text():
    assert ov.ids_in_text("done via #D52 and #1626") == {"#D52", "#1626"}
    assert ov.ids_in_text("no ids here") == set()
    assert ov.ids_in_text("") == set()


def test_ids_in_branch_letter_convention():
    assert ov.ids_in_branch("stuart/d40-read-file") == {"#D40"}
    assert ov.ids_in_branch("feature/D52-verify") == {"#D52"}


def test_ids_in_branch_ignores_short_numerics():
    # A bare 2-digit segment is too loose to be a reliable id.
    assert ov.ids_in_branch("release/v12") == set()
    # A 3+ digit ticket number is kept.
    assert ov.ids_in_branch("stuart/1626-popin") == {"#1626"}


def test_norm_id():
    assert ov._norm_id("d52") == "#D52"
    assert ov._norm_id("#1626") == "#1626"
    assert ov._norm_id("") == ""


# =============================================================================
# parse_board — #D43: INTERACTIVE / BLOCKED are their OWN lanes, never NOW
# =============================================================================

BOARD_TEXT = """# jules backlog

> intro

## Now

- [ ] **#D52 Effect-verification**. Verify. `[dev]`
- [x] **#D18 PR review card**. Shipped. *(board:#D18)*

## Blocked

- [ ] **#D51 Durable workspace**. Blocked on checkout.

## Interactive

- [ ] **#1626 Set-Address pop-in**. Needs a click.

## Completed

- [x] **#D41 Real repo list**. Done.
"""


def test_parse_board_preserves_every_lane():
    tickets = ov.parse_board(BOARD_TEXT)
    lanes = {t["lane"] for t in tickets}
    # Blocked + Interactive are NOT folded into Now (#D43).
    assert {"Now", "Blocked", "Interactive", "Completed"} <= lanes


def test_parse_board_done_flag_and_ids():
    tickets = {t["id"]: t for t in ov.parse_board(BOARD_TEXT)}
    assert tickets["#D18"]["done"] is True
    assert tickets["#D52"]["done"] is False
    assert tickets["#D51"]["lane"] == "Blocked"
    assert tickets["#1626"]["lane"] == "Interactive"


# =============================================================================
# reconcile() — THE PRODUCT
# =============================================================================

def test_reconcile_all_aligned_no_mismatch():
    board = {"tickets": [{"id": "#D40", "title": "read file", "lane": "Completed", "done": True}]}
    queue = {"items": [{"id": 1, "source": "board:#D40", "title": "read file",
                        "status": "done", "pr_url": "https://github.com/x/y/pull/70"}]}
    github = {"repos": [{"repo": "lucid-cove",
                         "open_prs": [],
                         "merged": [{"number": 70, "title": "#D40 scoped read_file",
                                     "head": "stuart/d40-read-file"}]}]}
    assert ov.reconcile(board, queue, github) == []


def test_reconcile_queue_done_no_pr():
    """The #D18/#D52 trap: queue says done, no PR anywhere proves it."""
    board = {"tickets": []}
    queue = {"items": [{"id": 5, "source": "board:#D18", "title": "PR review card",
                        "status": "done", "pr_url": ""}]}
    github = {"repos": [{"repo": "lucid-cove", "open_prs": [], "merged": []}]}
    out = ov.reconcile(board, queue, github)
    assert len(out) == 1
    assert out[0]["type"] == "queue_done_no_pr"
    assert out[0]["id"] == "#D18"


def test_reconcile_queue_done_with_merged_pr_is_clean():
    queue = {"items": [{"id": 5, "source": "board:#D18", "title": "card",
                        "status": "done", "pr_url": ""}]}
    github = {"repos": [{"repo": "lucid-cove", "open_prs": [],
                         "merged": [{"number": 61, "title": "#D18 tests", "head": "stuart/d18"}]}]}
    assert ov.reconcile({"tickets": []}, queue, github) == []


def test_reconcile_pr_open_not_in_queue():
    board = {"tickets": []}
    queue = {"items": []}
    github = {"repos": [{"repo": "lucid-cove", "merged": [],
                         "open_prs": [{"number": 72, "title": "#D60 new thing",
                                       "head": "stuart/d60-thing", "html_url": "u"}]}]}
    out = ov.reconcile(board, queue, github)
    assert len(out) == 1 and out[0]["type"] == "pr_open_not_in_queue"


def test_reconcile_open_pr_tracked_by_queue_is_clean():
    queue = {"items": [{"id": 9, "source": "board:#D60", "title": "thing",
                        "status": "assigned", "pr_url": ""}]}
    github = {"repos": [{"repo": "lucid-cove", "merged": [],
                         "open_prs": [{"number": 72, "title": "#D60 thing",
                                       "head": "stuart/d60", "html_url": "u"}]}]}
    assert ov.reconcile({"tickets": []}, queue, github) == []


def test_reconcile_board_done_untracked():
    """Board claims done, neither queue nor GitHub can confirm it."""
    board = {"tickets": [{"id": "#D99", "title": "phantom", "lane": "Completed", "done": True}]}
    out = ov.reconcile(board, {"items": []}, {"repos": []})
    assert len(out) == 1 and out[0]["type"] == "board_ticket_untracked"
    assert out[0]["id"] == "#D99"


def test_reconcile_board_not_done_is_normal_intake():
    """A non-done board ticket absent from the queue is normal intake, not a mismatch."""
    board = {"tickets": [{"id": "#D77", "title": "queued idea", "lane": "Now", "done": False}]}
    assert ov.reconcile(board, {"items": []}, {"repos": []}) == []


def test_reconcile_empty_inputs():
    assert ov.reconcile({}, {}, {}) == []
    assert ov.reconcile(None, None, None) == []


def test_reconcile_multiple_mismatches_across_sources():
    board = {"tickets": [{"id": "#D99", "title": "phantom", "lane": "Completed", "done": True}]}
    queue = {"items": [{"id": 5, "source": "board:#D18", "title": "card",
                        "status": "done", "pr_url": ""}]}
    github = {"repos": [{"repo": "lucid-cove", "merged": [],
                         "open_prs": [{"number": 72, "title": "#D60", "head": "x", "html_url": "u"}]}]}
    types = sorted(m["type"] for m in ov.reconcile(board, queue, github))
    assert types == ["board_ticket_untracked", "pr_open_not_in_queue", "queue_done_no_pr"]


# =============================================================================
# Route assembly — fake sources; a failing source is 'unavailable', not empty-ok
# =============================================================================

class _FakeReq:
    pass


async def test_ops_state_assembles_all_sources(monkeypatch):
    monkeypatch.setattr(ov, "_require_operator", lambda r: _async(True))
    monkeypatch.setattr(ov, "_fetch_intake", lambda: _async(
        {"tickets": [{"id": "#D1", "title": "t", "lane": "Now", "done": False}],
         "source": "nc:board", "fetched_at": 1.0, "error": None}))
    monkeypatch.setattr(ov, "_fetch_queue", lambda: _async(
        {"items": [], "approvals": [], "source": "db:steward_queue", "fetched_at": 1.0, "error": None}))
    monkeypatch.setattr(ov, "_fetch_github", lambda: _async(
        {"repos": [{"repo": "lucid-cove", "main_sha": "abc1234", "error": None,
                    "open_prs": [], "merged": []}], "owner": "LucidPrinciples", "fetched_at": 1.0, "error": None}))
    monkeypatch.setattr(ov, "_fetch_vault", lambda: _async(
        {"available": False, "error": "no vault", "fetched_at": 1.0}))
    monkeypatch.setattr(ov, "_fetch_watcher_open_count", lambda: _async(3))

    out = await ov.ops_state(_FakeReq())
    assert out["ok"] is True
    assert out["header"]["watcher_open"] == 3
    assert out["header"]["repos"][0]["main_sha"] == "abc1234"
    assert "mismatches" in out
    assert out["intake"]["source"] == "nc:board"


async def test_ops_state_failing_source_carries_error(monkeypatch):
    monkeypatch.setattr(ov, "_require_operator", lambda r: _async(True))
    monkeypatch.setattr(ov, "_fetch_intake", lambda: _async(
        {"tickets": [], "source": "nc:board", "fetched_at": 1.0, "error": "Board read failed (HTTP 423)"}))
    monkeypatch.setattr(ov, "_fetch_queue", lambda: _async({"items": [], "approvals": [], "source": "db", "fetched_at": 1.0, "error": None}))
    monkeypatch.setattr(ov, "_fetch_github", lambda: _async({"repos": [], "fetched_at": 1.0, "error": None}))
    monkeypatch.setattr(ov, "_fetch_vault", lambda: _async({"available": False, "fetched_at": 1.0, "error": None}))
    monkeypatch.setattr(ov, "_fetch_watcher_open_count", lambda: _async(None))
    out = await ov.ops_state(_FakeReq())
    # The failing board reports its error — it is NOT silently an empty column.
    assert out["intake"]["error"] == "Board read failed (HTTP 423)"


async def test_ops_state_scope_gate_blocks_non_operator(monkeypatch):
    monkeypatch.setattr(ov, "_require_operator", lambda r: _async(False))
    out = await ov.ops_state(_FakeReq())
    # JSONResponse with 403 — not the assembled state.
    assert getattr(out, "status_code", None) == 403


async def test_require_operator_open_in_single_mode(monkeypatch):
    monkeypatch.setattr(ov, "env", lambda k, d=None: "single" if k == "COVE_MODE" else d)
    assert await ov._require_operator(_FakeReq()) is True


async def test_require_operator_multi_requires_admin(monkeypatch):
    monkeypatch.setattr(ov, "env", lambda k, d=None: "multi" if k == "COVE_MODE" else d)
    import src.dashboard.routes.presence as presence
    monkeypatch.setattr(presence, "get_current_presence", lambda r: _async({"cove_role": "member"}))
    assert await ov._require_operator(_FakeReq()) is False
    monkeypatch.setattr(presence, "get_current_presence", lambda r: _async({"cove_role": "admin"}))
    assert await ov._require_operator(_FakeReq()) is True


async def _async(v):
    return v
