# Tests for the CF-65 carry-on-upgrade pure logic (no DB, no network).
from src.utils.carry import (
    intersect_columns,
    streak_from_date_counts,
    ACCOUNT_PREFS_KEEP,
)


def test_intersect_drops_unknown_and_excluded():
    row = {"id": 9, "presence_id": "hub-uuid", "session_id": "s1",
           "date": "2026-07-01", "brand_new_hub_col": "x"}
    local = {"id", "presence_id", "session_id", "date", "time", "bpm"}
    cols = intersect_columns(row.keys(), local)
    # id + presence_id never copied; unknown exporter columns dropped.
    assert cols == ["date", "session_id"]


def test_intersect_empty_when_nothing_matches():
    assert intersect_columns({"only_on_hub"}, {"session_id"}) == []


def test_streak_recompute_consecutive_run():
    counts = {"2026-06-28": 1, "2026-06-29": 2, "2026-06-30": 1, "2026-07-01": 1}
    v = streak_from_date_counts(counts, "2026-07-01")
    assert v["current_streak"] == 4
    assert v["longest_streak"] == 4
    assert v["total_sessions"] == 5
    assert v["this_month_sessions"] == 1
    assert v["last_tuning_date"] == "2026-07-01"


def test_streak_zero_when_run_already_broke():
    counts = {"2026-06-20": 1, "2026-06-21": 1}
    v = streak_from_date_counts(counts, "2026-07-01")
    assert v["current_streak"] == 0        # last tuning long before yesterday
    assert v["longest_streak"] == 2
    assert v["total_sessions"] == 2


def test_streak_survives_yesterday_gap_rule():
    # Tuned through yesterday but not yet today -> streak still alive.
    counts = {"2026-06-29": 1, "2026-06-30": 1}
    v = streak_from_date_counts(counts, "2026-07-01")
    assert v["current_streak"] == 2


def test_streak_longest_across_broken_runs():
    counts = {"2026-06-01": 1, "2026-06-02": 1, "2026-06-03": 1,
              "2026-06-10": 1, "2026-06-11": 1}
    v = streak_from_date_counts(counts, "2026-07-01")
    assert v["longest_streak"] == 3
    assert v["current_streak"] == 0


def test_streak_empty_and_malformed_dates():
    v = streak_from_date_counts({}, "2026-07-01")
    assert v["total_sessions"] == 0 and v["current_streak"] == 0
    v = streak_from_date_counts({"not-a-date": 3, "2026-07-01": 1}, "2026-07-01")
    assert v["total_sessions"] == 1        # malformed date rows don't count or crash
    assert v["current_streak"] == 1


def test_prefs_keep_list_is_conservative():
    # Safe default: nothing from the accounts.preferences blob carries until a
    # key is explicitly reviewed as account-level. Known blob keys today are all
    # per-Cove (features / posting / action_links) and must never sneak in.
    for cove_level in ("features", "posting", "action_links"):
        assert cove_level not in ACCOUNT_PREFS_KEEP
