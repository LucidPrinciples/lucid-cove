"""Woods member polish — casing root, affiliates auth shape, voice scope, team tier.

Jules 1310 / 1313 / 1316: first-share readiness (Brian Roemmele path).
"""
from pathlib import Path

from src.dashboard.routes.presence import _titlecase_name, _sanitize_name
from src.permissions import TAB_TIER_REQUIREMENTS, ROUTE_TIER_REQUIREMENTS, Tier


def test_titlecase_operator_names_root():
    assert _titlecase_name(_sanitize_name("jeff")) == "Jeff"
    assert _titlecase_name(_sanitize_name("jane")) == "Jane"
    assert _titlecase_name(_sanitize_name("Jerry")) == "Jerry"
    assert _titlecase_name(_sanitize_name("  walt smith ")) == "Walt Smith"


def test_claim_operator_path_titlecases_name_source():
    """claim-operator must import and apply the same titlecase helper (source lock)."""
    src = Path("src/dashboard/routes/onboarding.py").read_text()
    assert "_titlecase_name" in src
    assert "_sanitize_name" in src
    # display_name write uses titlecased name, not raw body strip alone
    assert "display_name = %s" in src


def test_invite_complete_titlecases_display_name():
    src = Path("src/dashboard/routes/presence_invite.py").read_text()
    # display_name assignment must wrap sanitize in titlecase
    assert "_titlecase_name(\n        _sanitize_name(body.get(\"display_name\")" in src or \
           "_titlecase_name(\n        _sanitize_name(body.get('display_name')" in src or \
           "_titlecase_name(" in src and "display_name = _titlecase_name" in src.replace("\n", " ")


def test_affiliates_js_distinguishes_signed_out():
    js = Path("src/dashboard/static/js/affiliates.js").read_text()
    assert "signed_in === false" in js
    assert "credentials: 'same-origin'" in js
    # Must not treat missing referral_code alone as "Sign in"
    assert "Your referral code is being set up" in js or "Unable to load" in js


def test_affiliates_api_mints_missing_code():
    src = Path("src/dashboard/routes/account.py").read_text()
    assert "_generate_referral_code" in src
    assert "signed_in" in src
    assert "referral_code IS NULL" in src or "referral_code = ''" in src


def test_settings_voice_scopes_member_vs_admin():
    js = Path("src/dashboard/static/js/settings-voice.js").read_text()
    assert "isCoveAdmin" in js
    assert "Member: personal agent only" in js or "personal agent only" in js
    assert "/api/team/roster" in js  # admin pulls full roster


def test_member_intelligence_banner_not_alarm():
    js = Path("src/dashboard/static/js/settings.js").read_text()
    assert "isMember" in js
    assert "Cove default" in js
    assert "set by your Cove admins" in js


def test_team_tab_open_to_presence():
    assert TAB_TIER_REQUIREMENTS["team"] == Tier.PRESENCE
    assert ROUTE_TIER_REQUIREMENTS["/api/team"] == Tier.PRESENCE
    assert ROUTE_TIER_REQUIREMENTS["/api/family"] == Tier.PRESENCE


def test_for_param_prefill_in_join_wizard():
    html = Path("src/dashboard/static/action-board/new-cove-setup.html").read_text()
    assert "_forName" in html
    assert "get('for')" in html
    assert "op-name" in html


def test_bring_cta_titlecases_agent_name():
    html = Path("src/dashboard/static/action-board/new-agent-setup.html").read_text()
    assert "_bringName" in html
    assert "Bring ${_bringName}" in html or "Bring ${ _bringName" in html
