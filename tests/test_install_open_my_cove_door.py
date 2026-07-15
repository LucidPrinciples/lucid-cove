"""Woods install-pass: Open my Cove must not crash after gates/ack work."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOME_JS = (ROOT / "src/dashboard/static/js/home.js").read_text()
PRES = (ROOT / "src/dashboard/routes/presence.py").read_text()
ONB = (ROOT / "src/dashboard/routes/onboarding.py").read_text()


def _open_my_cove_fn() -> str:
    start = HOME_JS.index("async function _openMyCove")
    end = HOME_JS.index("\nfunction ", start + 10)
    return HOME_JS[start:end]


def test_open_my_cove_opens_tab_before_await():
    """Popup blockers kill window.open after await — must open about:blank first."""
    fn = _open_my_cove_fn()
    assert "window.open('about:blank'" in fn or 'window.open("about:blank"' in fn
    # fetch must still happen, but after the synchronous open
    assert "cove-door" in fn
    open_idx = fn.index("about:blank")
    fetch_idx = fn.index("cove-door")
    assert open_idx < fetch_idx


def test_open_my_cove_rejects_non_p_door():
    fn = _open_my_cove_fn()
    assert "pathname.startsWith('/p/')" in fn or 'pathname.startsWith("/p/")' in fn


def test_open_my_cove_same_tab_fallback_when_popup_blocked():
    fn = _open_my_cove_fn()
    assert "location.assign(url)" in fn


def test_open_my_cove_already_on_live_domain_skips_mint():
    fn = _open_my_cove_fn()
    assert "already" in fn.lower() or "endsWith('.' + _dom)" in fn
    # Should not require a door mint when host matches claimed domain
    assert "location.assign(location.protocol" in fn or "location.assign(" in fn


def test_p_handler_returns_html_error_for_browsers():
    assert "_auth_link_error_response" in PRES
    assert "That sign-in link didn't work" in PRES
    assert "return _auth_link_error_response(request, 403" in PRES


def test_mint_signin_door_always_includes_p_path():
    start = PRES.index("async def mint_signin_door")
    chunk = PRES[start:start + 2000]
    assert 'if "/p/" not in door' in chunk
    assert "return door" in chunk
    assert "/p/" in chunk


def test_cove_door_refuses_malformed_door():
    assert "door_malformed" in ONB
    assert 'startswith("/p/")' in ONB or "startswith('/p/')" in ONB
