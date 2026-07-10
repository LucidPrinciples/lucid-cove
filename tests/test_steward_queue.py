"""Steward queue transition-matrix tests — the ONE definition shared by the
steward's tools (steward_queue_tools) and the operator's routes (steward_queue).
DB plumbing is exercised live; the judgment (what moves are legal) lives here."""

from src.tools.steward_queue_tools import VALID_STATUSES, can_transition


def test_all_statuses_covered_by_matrix():
    for s in VALID_STATUSES:
        # Every status must be a known key (terminal = empty set, not KeyError).
        assert can_transition(s, s) is True  # same-state updates always legal


def test_normal_forward_flow():
    assert can_transition("queued", "assigned")
    assert can_transition("assigned", "in_review")
    assert can_transition("in_review", "done")


def test_shortcuts_allowed():
    # Operator handled it directly / steward closes without a PR stage.
    assert can_transition("queued", "done")
    assert can_transition("assigned", "done")


def test_backward_moves():
    assert can_transition("assigned", "queued")       # un-take
    assert can_transition("in_review", "assigned")    # PR rejected, back to work
    assert not can_transition("in_review", "queued")  # no skipping back past owner


def test_drop_from_any_open_state():
    for s in ("queued", "assigned", "in_review"):
        assert can_transition(s, "dropped")


def test_terminal_states_stay_terminal():
    for terminal in ("done", "dropped"):
        for target in ("queued", "assigned", "in_review", "done", "dropped"):
            if target == terminal:
                continue
            assert not can_transition(terminal, target)


def test_unknown_status_is_never_legal():
    assert not can_transition("queued", "bogus")
    assert not can_transition("bogus", "queued")
