"""SIGNIN3 — signed-out dashboard panel instead of an empty board."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "src" / "dashboard" / "app.py").read_text()
CORE = (ROOT / "src" / "dashboard" / "static" / "js" / "core.js").read_text()
HOME = (ROOT / "src" / "dashboard" / "static" / "js" / "home.js").read_text()


def test_middleware_403_includes_signed_out_code():
    assert '"code": "signed_out"' in APP
    assert '"detail": "Authentication required"' in APP
    assert "status_code=403" in APP


def test_signed_out_panel_copy():
    assert "You're signed out on this device." in CORE
    assert "./cove-lifecycle.sh door" in CORE
    assert "Get a sign-in link by email." in CORE
    assert "/api/account/signin" in CORE


def test_fetch_wrapper_stops_after_first_signed_out():
    assert "window.__lpSignedOut" in CORE
    assert "showSignedOutPanel" in CORE
    assert "clearInterval(window.__lpStatusTimer)" in CORE
    assert "lpSignedOutFetch" in CORE
    assert "body.code === 'signed_out'" in CORE


def test_home_poll_noops_when_signed_out():
    assert "if (window.__lpSignedOut) return;" in HOME
    assert "if (window.__lpSignedOut) return;" in CORE
