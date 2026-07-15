"""Jules reinstall 2315 — remaining onboard polish on top of #131.

#131 already shipped: no Open my Cove on pending card, cove-door address_not_live,
basic resolve_cove_name for brain-ack. This covers the Hulton screenshots extras:
one address card (no dual UI), host-aware localhost copy, stronger New Cove scrub.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOME_JS = (ROOT / "src/dashboard/static/js/home.js").read_text()
WAKE = (ROOT / "src/dashboard/routes/wake_thread.py").read_text()
ONB = (ROOT / "src/dashboard/routes/onboarding.py").read_text()


def _host_command_card_block() -> str:
    start = HOME_JS.index("if (item.host_command)")
    end = HOME_JS.index("const sub = ESC(item.cove_subdomain", start)
    return HOME_JS[start:end]


def test_pending_host_command_card_still_has_no_open_my_cove():
    """#131 guarantee — keep regression-locked."""
    block = _host_command_card_block()
    assert "I ran the command — mark live" in block
    assert "Open my Cove &#8599;" not in block
    assert "_openMyCove" not in block


def test_save_domain_host_command_uses_canonical_checklist():
    """Jules 2315: post-save must not paint a second inline card."""
    assert "Jules 2315: self-host / co-located still owes the host command" in HOME_JS
    assert "await loadHomeApprovals()" in HOME_JS
    # Old dual-UI copy from the saveDomain host_command path must be gone.
    assert "Run this command first (do not skip)" not in HOME_JS


def test_done_card_host_aware_localhost_copy():
    assert "_onLive" in HOME_JS
    assert "leftover localhost tab" in HOME_JS
    assert "you're already on the live address" in HOME_JS
    # User-facing template must not hard-code the old close-localhost instruction.
    assert "then close this localhost tab" not in HOME_JS
    assert "Close this localhost tab" not in HOME_JS


def test_brain_ack_stronger_new_cove_scrub():
    assert "resolve_cove_name" in WAKE
    assert "_usable_cove_name" in WAKE
    assert "family_name" in WAKE
    # Force-replace residual model output.
    assert r"\bNew Cove\b" in WAKE or "New Cove" in WAKE


def test_cove_door_still_refuses_when_not_live():
    """#131 guarantee — keep regression-locked."""
    assert "address_not_live" in ONB
    assert "pending_host_command" in ONB
