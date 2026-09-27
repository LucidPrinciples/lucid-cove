"""SIGNIN4 — leftover magic-link product wording is gone."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(*parts: str) -> str:
    return (ROOT.joinpath(*parts)).read_text()


def test_dead_magic_link_routes_are_gone():
    app = _read("src", "dashboard", "app.py")
    rate = _read("src", "dashboard", "rate_limit.py")
    assert "/api/account/magic-link" not in app
    assert "/api/account/verify-magic-link" not in app
    assert "/api/account/magic-link" not in rate
    assert "/api/account/signin" in app
    assert "/api/account/signin" in rate


def test_auth_method_is_signin_link():
    example = _read("config", "cove.yaml.example")
    provisioner = _read("provision", "centralized.py")
    defaults = _read("src", "config.py")
    assert "method: signin_link" in example
    assert "method: magic_link" not in example
    assert '"method": "signin_link"' in provisioner
    assert '"method": "magic_link"' not in provisioner
    assert '"method": "signin_link"' in defaults


def test_setup_pages_use_signin_link_class():
    dictate = _read("src", "dashboard", "static", "action-board", "dictate-agent-setup.html")
    quick = _read("src", "dashboard", "static", "action-board", "quick-agent-setup.html")
    assert 'class="signin-link"' in dictate
    assert 'class="signin-link"' in quick
    assert 'class="magic"' not in dictate
    assert 'class="magic"' not in quick


def test_manifesto_not_magic_untouched():
    manifesto = _read("data", "knowledge-base", "manifesto.md")
    assert "not magic" in manifesto.lower()
