""" /api/config always exposes agent.last_tuned_at (null if no row). """

from src.dashboard.routes import core as core_mod
from src.dashboard.routes.core import (
    agent_tune_fields,
    host_tune_agent_ids,
    merge_tune_source,
)


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


def test_host_ids_admin_uses_steward_then_stuart(monkeypatch):
    monkeypatch.setattr(core_mod, "get_instance", lambda: {"type": "admin"})
    monkeypatch.setattr(
        core_mod, "get_steward_channel_config", lambda: {"agent_id": "stuart"}
    )
    assert host_tune_agent_ids() == ["stuart"]


def test_host_ids_domain_still_includes_stuart(monkeypatch):
    monkeypatch.setattr(core_mod, "get_instance", lambda: {"type": "domain"})
    monkeypatch.setattr(core_mod, "get_steward_channel_config", lambda: None)
    assert host_tune_agent_ids() == ["stuart"]


def test_host_ids_personal_uses_primary(monkeypatch):
    monkeypatch.setattr(core_mod, "get_instance", lambda: {"type": "personal"})
    monkeypatch.setattr(core_mod, "get_primary_agent_id", lambda: "atlas")
    assert host_tune_agent_ids() == ["atlas"]


def test_merge_prefers_agent_state_stamp():
    out = merge_tune_source(
        {"last_tuned_at": "state-ts", "last_frequency": "Trust", "last_echo_num": 1},
        {"tuned_at": "echo-ts", "frequency": "Peace", "echo_num": 9},
    )
    assert out["last_tuned_at"] == "state-ts"
    assert out["last_frequency"] == "Trust"
    assert out["last_echo_num"] == 1


def test_merge_falls_back_to_echo_when_state_stamp_missing():
    out = merge_tune_source(
        {"last_tuned_at": None, "last_frequency": None, "last_echo_num": 0},
        {"tuned_at": "echo-ts", "frequency": "Peace", "echo_num": 129},
    )
    assert out["last_tuned_at"] == "echo-ts"
    assert out["last_frequency"] == "Peace"
    assert out["last_echo_num"] == 129
