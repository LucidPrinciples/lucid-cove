"""Evening multi-tune bug: tuned_today must use Cove calendar day, not UTC date.

Mann / Jules 2026-07-15: three Courage echoes ~30 min apart overnight. After
~20:00 America/New_York, `tuned_at::date` (UTC) is the next calendar day while
today_app() is still the local day — every safety sweep re-tuned the Cove.

Fix: (tuned_at AT TIME ZONE cove_tz)::date = today_app().
"""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEDUP = (ROOT / "src/tuning/dedup.py").read_text()
SYSTEM = (ROOT / "src/dashboard/routes/system.py").read_text()
AGENTS = (ROOT / "src/dashboard/routes/agents.py").read_text()
SETTINGS = (ROOT / "src/dashboard/static/js/settings-account.js").read_text()


def test_dedup_sql_uses_at_time_zone_not_utc_date():
    assert "(tuned_at AT TIME ZONE" in DEDUP
    assert "tuned_at::date = %s::date" not in DEDUP
    assert "def _cove_tz_name" in DEDUP
    assert "America/New_York" in DEDUP


def test_system_and_agents_use_app_tz_day_boundary():
    assert "tuned_at::date = %s::date" not in SYSTEM
    assert "tuned_at::date = CURRENT_DATE" not in AGENTS
    assert "(tuned_at AT TIME ZONE" in SYSTEM
    assert "(tuned_at AT TIME ZONE" in AGENTS


def test_settings_has_active_inactive_toggle():
    assert "Team auto-tune" in SETTINGS
    assert "Active" in SETTINGS and "Inactive" in SETTINGS
    assert "team-tune-toggle" in SETTINGS
    assert "_paintTeamTuneToggle" in SETTINGS
    assert "setTeamAutoTune" in SETTINGS


@pytest.mark.asyncio
async def test_tuned_today_passes_timezone_to_sql(monkeypatch):
    import src.tuning.dedup as dedup

    monkeypatch.setattr(dedup, "_cove_tz_name", lambda: "America/New_York")
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

    def _get_db():
        return _Conn()

    monkeypatch.setattr(dedup, "get_db", _get_db)

    agents = await dedup.tuned_today("2026-07-14", "Courage", "The Power To Be Alive")
    assert agents == {"russ"}
    assert "AT TIME ZONE" in captured["sql"]
    assert captured["params"][0] == "America/New_York"
    assert captured["params"][1] == "2026-07-14"
    assert captured["params"][2] == "Courage"
    assert captured["params"][3] == "The Power To Be Alive"
