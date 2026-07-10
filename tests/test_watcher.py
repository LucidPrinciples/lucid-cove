"""Watcher pure-logic tests — the error classifier (#D13 shapes) and the
push-without-PR correlation. DB checks are exercised live (the watcher only
reads facts and writes its own table); the judgment calls live here."""

from datetime import datetime, timezone, timedelta

from src.utils.watcher import looks_like_error, pushes_without_pr


# ── looks_like_error: the exact shapes that hid in approval_requests.result ──

def test_flags_shell_exit_codes():
    assert looks_like_error("sh: 1: gh: not found [exit: 127]")


def test_flags_error_prefix():
    assert looks_like_error("Error: could not read Username for 'https://github.com'")


def test_flags_traceback():
    assert looks_like_error("Traceback (most recent call last):\n  File ...")


def test_flags_http_422():
    assert looks_like_error('HTTP 422: Validation Failed — head invalid')


def test_flags_failed_verb():
    assert looks_like_error("push FAILED: remote rejected")


def test_empty_result_is_not_error():
    # Many tools return nothing on success — silence is not failure here.
    assert not looks_like_error("")
    assert not looks_like_error(None)
    assert not looks_like_error("   \n ")


def test_success_output_is_not_error():
    assert not looks_like_error("PR CREATED: #43 https://github.com/x/y/pull/43")
    assert not looks_like_error("Pushed branch stuart/fix-thing to origin.")
    assert not looks_like_error("3 files changed, 47 insertions(+), 4 deletions(-)")


# ── pushes_without_pr: the #D9 gap (branch pushed, PR never requested) ────────

def _t(hours_ago: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=hours_ago)


def _push(branch: str, hours_ago: float, rid: str = "p1") -> dict:
    return {"request_id": rid, "args": {"branch": branch}, "created_at": _t(hours_ago)}


def _pr(branch: str, hours_ago: float) -> dict:
    return {"args": {"branch": branch}, "created_at": _t(hours_ago)}


def test_push_with_matching_pr_after_is_covered():
    assert pushes_without_pr([_push("stuart/fix-a", 6)], [_pr("stuart/fix-a", 5)]) == []


def test_push_with_no_pr_is_orphaned():
    orphans = pushes_without_pr([_push("stuart/fix-a", 6)], [])
    assert len(orphans) == 1 and orphans[0]["request_id"] == "p1"


def test_pr_before_the_push_does_not_cover_it():
    # A PR requested BEFORE the push belongs to earlier work.
    assert len(pushes_without_pr([_push("stuart/fix-a", 3)], [_pr("stuart/fix-a", 6)])) == 1


def test_pr_for_a_different_branch_does_not_cover():
    assert len(pushes_without_pr([_push("stuart/fix-a", 6)], [_pr("stuart/fix-b", 5)])) == 1


def test_branchless_pr_covers_any_push_after_it():
    # Older schema rows / defaulted args: don't false-alarm when we can't compare.
    assert pushes_without_pr([_push("stuart/fix-a", 6)], [_pr("", 5)]) == []


def test_branchless_push_covered_by_any_later_pr():
    assert pushes_without_pr([_push("", 6)], [_pr("stuart/whatever", 5)]) == []
