"""#D49 — merge-feedback. The matching logic (parse_pr_url + plan_merge_feedback)
is pure and carries the correctness weight: only merged PRs get annotated, never
advanced to done, and re-runs are idempotent."""
from src.tools.merge_feedback import parse_pr_url, plan_merge_feedback


# ---- parse_pr_url ----

def test_parse_pr_url_basic():
    assert parse_pr_url("https://github.com/LucidPrinciples/lucid-cove/pull/72") \
        == ("LucidPrinciples/lucid-cove", 72)


def test_parse_pr_url_embedded_and_trailing():
    assert parse_pr_url("see https://github.com/o/r/pull/5 now") == ("o/r", 5)
    assert parse_pr_url("https://github.com/o/r/pull/5/files") == ("o/r", 5)


def test_parse_pr_url_none():
    assert parse_pr_url("") is None
    assert parse_pr_url(None) is None
    assert parse_pr_url("https://github.com/o/r/issues/5") is None
    assert parse_pr_url("board:#D18") is None


# ---- plan_merge_feedback ----

def test_plan_annotates_only_merged():
    rows = [
        {"id": 1, "title": "A", "pr_url": "https://github.com/o/r/pull/10", "notes": ""},
        {"id": 2, "title": "B", "pr_url": "https://github.com/o/r/pull/11", "notes": ""},
    ]
    out = plan_merge_feedback(rows, {10})   # only #10 merged
    assert [a["id"] for a in out] == [1]
    assert out[0]["pr_number"] == 10
    assert "merged != deployed" in out[0]["note"]


def test_plan_idempotent_skips_already_noted():
    rows = [{"id": 1, "title": "A", "pr_url": "https://github.com/o/r/pull/10",
             "notes": "prior · merged (PR #10) — merged != deployed"}]
    assert plan_merge_feedback(rows, {10}) == []


def test_plan_skips_rows_without_pr_url():
    rows = [{"id": 1, "title": "A", "pr_url": "", "notes": ""},
            {"id": 2, "title": "B", "pr_url": None, "notes": ""}]
    assert plan_merge_feedback(rows, {10, 11}) == []


def test_plan_note_never_advances_status():
    # The plan carries only an annotation note — no 'status' key — so the caller
    # (which passes note= to _update) can never move the row to done. merged !=
    # deployed is the invariant.
    rows = [{"id": 3, "title": "C", "pr_url": "https://github.com/o/r/pull/9", "notes": ""}]
    out = plan_merge_feedback(rows, {9})
    assert set(out[0].keys()) == {"id", "pr_number", "title", "note"}
    assert "status" not in out[0]


def test_plan_multiple_mixed():
    rows = [
        {"id": 1, "title": "A", "pr_url": "https://github.com/o/r/pull/1", "notes": ""},
        {"id": 2, "title": "B", "pr_url": "https://github.com/o/r/pull/2", "notes": "merged (PR #2) — merged != deployed"},
        {"id": 3, "title": "C", "pr_url": "https://github.com/o/r/pull/3", "notes": ""},
        {"id": 4, "title": "D", "pr_url": "board:#D9", "notes": ""},
    ]
    out = plan_merge_feedback(rows, {1, 2, 3})
    # #1 merged+unnoted -> in; #2 already noted -> skip; #3 merged+unnoted -> in;
    # #4 no pr_url -> skip.
    assert [a["id"] for a in out] == [1, 3]


def test_plan_empty_inputs():
    assert plan_merge_feedback([], set()) == []
    assert plan_merge_feedback([{"id": 1, "pr_url": "https://github.com/o/r/pull/1", "notes": ""}], set()) == []
