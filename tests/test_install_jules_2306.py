"""Jules reinstall 2306 — address door order + brain-ack cove name."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOME_JS = (ROOT / "src/dashboard/static/js/home.js").read_text()
WAKE = (ROOT / "src/dashboard/routes/wake_thread.py").read_text()
ONB = (ROOT / "src/dashboard/routes/onboarding.py").read_text()


def _host_command_card_block() -> str:
    """Slice of home.js that renders the pending host_command address card."""
    start = HOME_JS.index("if (item.host_command)")
    end = HOME_JS.index("const sub = ESC(item.cove_subdomain", start)
    return HOME_JS[start:end]


def test_pending_host_command_card_has_no_open_my_cove():
    block = _host_command_card_block()
    assert "I ran the command — mark live" in block
    # Helper copy may name the door; the clickable door itself must not ship yet.
    assert "Open my Cove &#8599;" not in block
    assert "_openMyCove" not in block


def test_save_domain_pending_path_has_no_open_my_cove_before_mark_live():
    # The post-save host_command branch (not fully_live, no DNS records).
    marker = "Run the host command first. Mark live only after it succeeds"
    assert marker in HOME_JS
    # Fully-live path may still offer Open my Cove — that's fine. Pending path must not.
    idx = HOME_JS.index(marker)
    # Nearby window around the pending path should not reintroduce the door button.
    window = HOME_JS[max(0, idx - 600): idx + 200]
    assert "Open my Cove &#8599;" not in window


def test_open_my_cove_surfaces_server_error():
    assert "address_not_live" in ONB or "Address isn't live yet" in ONB
    assert "credentials: 'same-origin'" in HOME_JS
    assert "d && d.error" in HOME_JS or "d.error" in HOME_JS


def test_brain_ack_uses_resolve_cove_name():
    assert "resolve_cove_name" in WAKE
    assert 'cove_name = (env("COVE_NAME") or "").strip() or "this Cove"' not in WAKE
    # Still has a New Cove guard so the seed never wins.
    assert "new cove" in WAKE.lower()


def test_cove_door_refuses_when_pending_host_command():
    assert "pending_host_command" in ONB
    assert "address_not_live" in ONB
    assert "I ran the command — mark live" in ONB
