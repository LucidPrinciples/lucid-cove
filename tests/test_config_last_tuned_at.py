""" /api/config always exposes agent.last_tuned_at (null if no row). """

from src.dashboard.routes.core import agent_tune_fields


def test_no_row_still_has_last_tuned_at_key():
    out = agent_tune_fields(None)
    assert "last_tuned_at" in out
    assert out["last_tuned_at"] is None
    assert out["last_frequency"] is None
    assert out["last_echo_num"] is None


def test_row_with_timestamp():
    out = agent_tune_fields(
        {"last_tuned_at": "2026-11-06 12:00:00+00", "last_frequency": "Trust", "last_echo_num": 272}
    )
    assert out["last_tuned_at"] == "2026-11-06 12:00:00+00"
    assert out["last_frequency"] == "Trust"
    assert out["last_echo_num"] == 272


def test_row_with_null_timestamp():
    out = agent_tune_fields(
        {"last_tuned_at": None, "last_frequency": None, "last_echo_num": None}
    )
    assert out["last_tuned_at"] is None
