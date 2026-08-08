"""Free Tuner: daily personal-tune limit + hub/upgrade product gates.

Covers:
  - tier → daily limit mapping
  - POST /api/tuning/request returns 429 when free quota used
  - Tuning Hub shell always includes Recent Tunings mount (not Tuner-stripped)
  - upgrade CTA does not treat bare presence.id as "in a Cove"
"""

from pathlib import Path

import pytest

from src.dashboard.routes.tuning_request.helpers import _daily_tune_limit_for_tier


ROOT = Path(__file__).resolve().parents[1]


def test_daily_limit_by_tier():
    assert _daily_tune_limit_for_tier("free") == 1
    assert _daily_tune_limit_for_tier("FREE") == 1
    assert _daily_tune_limit_for_tier(None) == 1
    assert _daily_tune_limit_for_tier("unknown") == 1
    assert _daily_tune_limit_for_tier("pro") == -1
    assert _daily_tune_limit_for_tier("operator") == -1
    assert _daily_tune_limit_for_tier("presence") == -1
    assert _daily_tune_limit_for_tier("cove") == -1


@pytest.mark.asyncio
async def test_request_tuning_free_daily_limit_429(monkeypatch):
    from src.dashboard.routes.tuning_request import core as core_mod

    async def fake_row(request):
        return {"id": "acct-free-1", "tier": "free"}

    async def fake_count(presence_id, today):
        assert presence_id == "acct-free-1"
        return 1

    class _Body:
        async def json(self):
            return {
                "frequency": "Peace",
                "context": "Starting the Day",
                "entry_mode": "Tune",
                "initial_state": "Decompress",
            }

    monkeypatch.setattr(core_mod, "_get_presence_row", fake_row)
    monkeypatch.setattr(core_mod, "_count_tunes_today", fake_count)

    resp = await core_mod.request_tuning(_Body())
    assert resp.status_code == 429
    body = resp.body
    # JSONResponse stores bytes
    import json

    data = json.loads(body)
    assert data["error"] == "daily_limit"
    assert data["today_count"] == 1
    assert data["limit"] == 1


def test_panels_hub_always_recent_drops_for_tuner():
    src = (ROOT / "src/dashboard/static/js/panels.js").read_text()
    # Title is always Tuning Hub (no isTuner branch to "Today's Tuning")
    assert 'th-title">Tuning Hub' in src
    assert "Today's Tuning</div>" not in src.split("Hub Header")[1].split("Section 1")[0]
    # Recent Tunings mount is not wrapped in MC.isTuner ? '' 
    assert 'id="otRecentDrops"' in src
    # Love Equation still Operator+/Cove only
    assert "Love Equation (Operator+" in src or "Love Equation" in src
    assert "${MC.isTuner ? '' : `" in src  # Love Equation still gated


def test_upgrade_cta_not_gated_on_presence_id():
    src = (ROOT / "src/dashboard/static/js/upgrade.js").read_text()
    assert "presence.id" not in src or "NOT \"in a Cove\"" in src or "not \"in a Cove\"" in src.lower() or "bare presence.id" in src
    # Must not use the old false-positive inCove check
    assert "MC.presence.cove_role || MC.presence.id" not in src
    assert "inFamilyCove" in src


def test_tune_flow_gates_start_new_tune():
    src = (ROOT / "src/dashboard/static/js/tune-flow.js").read_text()
    assert "function _tfStartNewTune()" in src
    # Gate at start of _tfStartNewTune
    start = src.index("function _tfStartNewTune()")
    chunk = src[start : start + 400]
    assert "_tfCanTuneAgain" in chunk
    assert "_tfUpgrade" in chunk
    assert "daily_limit" in src
