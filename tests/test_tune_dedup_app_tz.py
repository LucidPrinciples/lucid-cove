"""Dedup is Drop identity — never calendar day.

Operator policy 2026-07-15: backend tuning is "has this agent applied the
latest Drop?" not "did something run today/tomorrow?". Calendar-day dedup
regressed repeatedly (UTC vs Cove-local → triple overnight Courage on Mann).

Identity = frequency + principle + tuning_key (key preferred).
"""
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEDUP = (ROOT / "src/tuning/dedup.py").read_text()
SWEEP = (ROOT / "src/tuning/sweep.py").read_text()
PRESENCE = (ROOT / "src/tuning/presence_tune.py").read_text()
DISPATCH = (ROOT / "src/graphs/ltp/dispatch.py").read_text()
SYSTEM = (ROOT / "src/dashboard/routes/system.py").read_text()
WATCHER = (ROOT / "src/utils/watcher.py").read_text()
SETTINGS = (ROOT / "src/dashboard/static/js/settings-account.js").read_text()


def test_dedup_has_no_calendar_sql():
    assert "tuned_at::date" not in DEDUP
    assert "AT TIME ZONE" not in DEDUP  # package identity, not day boundary
    assert "def tuned_for_package" in DEDUP
    assert "tuning_key" in DEDUP


def test_call_sites_pass_tuning_key():
    assert "tuned_today(today, _pkg_freq, _pkg_prin, _pkg_key)" in SWEEP
    assert "tuned_today(today, _freq, _prin, _key)" in PRESENCE
    assert 'package.get("tuning_key")' in DISPATCH


def test_system_and_watcher_use_package_not_date():
    assert "tuned_for_package" in SYSTEM
    assert "already applied latest Drop" in SYSTEM
    assert "tuned_at::date" not in SYSTEM
    assert "tuned_for_package" in WATCHER
    assert "get_todays_tuning" in WATCHER
    assert "tuned_today(today_app())" not in WATCHER


def test_settings_has_active_inactive_toggle():
    assert "Team auto-tune" in SETTINGS
    assert "Active" in SETTINGS and "Inactive" in SETTINGS
    assert "team-tune-toggle" in SETTINGS
    assert "_paintTeamTuneToggle" in SETTINGS


@pytest.mark.asyncio
async def test_tuned_for_package_matches_key_not_date(monkeypatch):
    import src.tuning.dedup as dedup

    captured = {}

    class _Conn:
        async def execute(self, sql, params=None):
            captured["sql"] = sql
            captured["params"] = params
            r = MagicMock()

            async def fetchall():
                return [{"agent_id": "russ"}]

            r.fetchall = fetchall
            return r

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(dedup, "get_db", lambda: _Conn())

    agents = await dedup.tuned_for_package(
        "Courage",
        "The Power To Be Alive",
        "As I look into the future I know we don't want the same again",
    )
    assert agents == {"russ"}
    assert "tuned_at" not in captured["sql"]
    assert "frequency" in captured["sql"]
    assert "tuning_key" in captured["sql"]
    assert captured["params"][0] == "Courage"
    assert "same again" in captured["params"][2]


@pytest.mark.asyncio
async def test_tuned_today_ignores_today_arg(monkeypatch):
    import src.tuning.dedup as dedup

    called = {}

    async def _tfp(freq, principle, tuning_key=""):
        called["args"] = (freq, principle, tuning_key)
        return {"a"}

    monkeypatch.setattr(dedup, "tuned_for_package", _tfp)
    out = await dedup.tuned_today("2099-01-01", "Courage", "P", "KEY")
    assert out == {"a"}
    assert called["args"] == ("Courage", "P", "KEY")


@pytest.mark.asyncio
async def test_tuned_today_no_package_returns_empty(monkeypatch):
    import src.tuning.dedup as dedup

    out = await dedup.tuned_today("2026-07-14")
    assert out == set()
