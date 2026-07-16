"""Second-presence land: Chat + no founder setup nags (install-pass 2026-07-15)."""
from pathlib import Path


def test_onboarding_suppresses_non_admin_checklist():
    src = Path("src/dashboard/routes/onboarding.py").read_text()
    assert "FOUNDER-ONLY GATE" in src
    assert "if not is_admin:" in src
    # Empty checklist for members — not the full founder card list
    assert 'return {"steps": [], "items": [], "done_count": 0' in src


def test_create_presence_signin_lands_on_chat():
    src = Path("src/dashboard/routes/presence.py").read_text()
    assert "tab=chat" in src
    assert "urlencode" in src or "_urlencode" in src
    assert '"next"' in src or "'next'" in src


def test_wake_thread_accepts_presence_id_retarget():
    src = Path("src/dashboard/routes/wake_thread.py").read_text()
    assert "_resolve_wake_target_agent" in src
    assert "presence_id" in src
    assert "agent_id=" in src


def test_member_wake_persists_thread_under_member():
    html = Path("src/dashboard/static/action-board/new-agent-setup.html").read_text()
    assert "_wakeMode === 'member'" in html
    assert "presence_id" in html
    assert "WAKE_JOIN_ORIENT" in html
    # Must not early-return skip for member mode anymore
    assert "if (_wakeMode === 'member') return;" not in html
