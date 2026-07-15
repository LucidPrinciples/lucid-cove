"""Woods polish follow-ups — Jules 1346 / 1353 / 1357."""
from pathlib import Path


def test_profile_email_optional_on_patch():
    src = Path("src/dashboard/routes/presence.py").read_text()
    assert "Email is optional" in src or "email is optional" in src.lower()
    assert 'raise HTTPException(400, "Email cannot be empty")' not in src


def test_upgrade_cta_hidden_inside_cove():
    js = Path("src/dashboard/static/js/upgrade.js").read_text()
    assert "inCove" in js
    assert "appLadderOnly" in js
    assert "Build a Cove" in js  # still exists for app ladder


def test_team_auto_tune_settings_controls():
    js = Path("src/dashboard/static/js/settings-account.js").read_text()
    assert "setTeamAutoTune" in js
    assert "team-tuning/disable" in js
    assert "refreshTeamTuneSettings" in js
    py = Path("src/tuning/team_consent.py").read_text()
    assert "def disable_team_auto_tune" in py
    routes = Path("src/dashboard/routes/onboarding.py").read_text()
    assert "team-tuning/disable" in routes


def test_settings_group_visual_separation():
    css = Path("src/dashboard/static/css/dashboard.css").read_text()
    assert "settings-group" in css
    assert "border-radius: 10px" in css or "border-radius:10px" in css
