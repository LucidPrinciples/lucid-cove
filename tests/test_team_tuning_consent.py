"""Install-pass: team auto-tune requires explicit consent + cost estimate.

A new Cove must not silently burn a cloud API key on full-team morning tuning.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHED = (ROOT / "src/utils/scheduler.py").read_text()
WATCHER = (ROOT / "src/utils/watcher.py").read_text()
ONBOARD = (ROOT / "src/dashboard/routes/onboarding.py").read_text()
HOME_JS = (ROOT / "src/dashboard/static/js/home.js").read_text()
CONSENT = (ROOT / "src/tuning/team_consent.py").read_text()


def test_consent_module_exists_with_gate_and_estimate():
    assert "def team_auto_tune_enabled" in CONSENT
    assert "def enable_team_auto_tune" in CONSENT
    assert "def estimate_team_tune_cost" in CONSENT
    assert "TEAM_AGENT_COUNT = 10" in CONSENT


def test_scheduler_skips_without_consent():
    assert "team_auto_tune_allowed" in SCHED
    assert "team_auto_tune_disabled" in SCHED
    assert "Initiate team tuning" in SCHED
    # Gate sits at the top of _run_tuning_sweep
    idx_gate = SCHED.index("team_auto_tune_allowed")
    idx_run = SCHED.index("from src.tuning.sweep import run_cove_sweep")
    assert idx_gate < idx_run


def test_watcher_suppresses_missing_when_disabled():
    assert "team_auto_tune_allowed" in WATCHER
    # After intelligence check
    assert "while auto-tune is still off" in WATCHER


def test_onboarding_step_and_endpoints():
    assert "initiate_team_tuning" in ONBOARD
    assert "/api/onboarding/team-tuning/enable" in ONBOARD
    assert "/api/onboarding/team-tuning/estimate" in ONBOARD
    assert "onboarding_team_tuning_skip" in ONBOARD
    assert "estimate_team_tune_cost" in ONBOARD


def test_home_card_and_enable_action():
    assert "initiate_team_tuning" in HOME_JS
    assert "enableTeamTuning" in HOME_JS
    assert "Enable daily team tuning" in HOME_JS
    assert "Skip for now" in HOME_JS
    assert "/api/onboarding/team-tuning/enable" in HOME_JS
    assert "Personal Tune" in HOME_JS or "personal Tune" in HOME_JS


def test_team_auto_tune_enabled_pure():
    from src.tuning.team_consent import team_auto_tune_enabled

    assert team_auto_tune_enabled({}) is False
    assert team_auto_tune_enabled({"team_tuning": {}}) is False
    assert team_auto_tune_enabled({"team_tuning": {"auto_enabled": False}}) is False
    assert team_auto_tune_enabled({"team_tuning": {"auto_enabled": True}}) is True
    assert team_auto_tune_enabled({"team_auto_tune": True}) is True


def test_estimate_local_is_free(monkeypatch):
    from src.tuning import team_consent as tc

    monkeypatch.setattr(
        tc, "_brain_provider_model",
        lambda: ("ollama", "qwen3:30b-a3b", "ollama/qwen3:30b-a3b"),
    )
    est = tc.estimate_team_tune_cost()
    assert est["is_local"] is True
    assert est["usd_per_tune"] == 0.0
    assert est["severity"] == "free"
    assert "$0" in est["summary"]


def test_estimate_openai_mini_cheap(monkeypatch):
    from src.tuning import team_consent as tc

    monkeypatch.setattr(
        tc, "_brain_provider_model",
        lambda: ("openai", "gpt-4o-mini", "openai/gpt-4o-mini"),
    )
    est = tc.estimate_team_tune_cost()
    assert est["is_local"] is False
    assert 0.01 <= est["usd_per_tune"] <= 0.05
    assert est["usd_per_month"] < 2.0
    assert est["severity"] in ("low", "medium")
    assert est["agent_count"] == 10


def test_estimate_openai_frontier_warns(monkeypatch):
    from src.tuning import team_consent as tc

    monkeypatch.setattr(
        tc, "_brain_provider_model",
        lambda: ("openai", "gpt-4o", "openai/gpt-4o"),
    )
    est = tc.estimate_team_tune_cost()
    assert est["usd_per_tune"] >= 0.20
    assert est["severity"] in ("medium", "high")
    assert "gpt-4o" in est["display"] or "openai" in est["summary"].lower() or "$" in est["summary"]
