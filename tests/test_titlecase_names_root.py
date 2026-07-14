"""Jules 2230 — capitalize operator + agent names at the root."""
from src.dashboard.routes.presence import _titlecase_name
from src.dashboard.routes.flow_cove import _titlecase_words


def test_titlecase_agent_and_operator():
    assert _titlecase_name("hal") == "Hal"
    assert _titlecase_name("walt") == "Walt"
    assert _titlecase_name("mcLeod") == "McLeod"
    assert _titlecase_words("hal") == "Hal"
    assert _titlecase_words("walt smith") == "Walt Smith"


def test_onboarding_pending_command_keeps_address_open():
    # Pure logic mirror of the onboarding address_done rule.
    def _live(cc):
        pending = (cc.get("pending_host_command") or "").strip()
        if "domain_live" in cc:
            live = bool(cc.get("domain_live"))
        else:
            live = not bool(pending)
        if pending:
            live = False
        return live

    assert _live({"domain": "x.lucidcove.org", "pending_host_command": "python3 set_domain.py"}) is False
    assert _live({"domain": "x.lucidcove.org", "domain_live": True, "pending_host_command": "cmd"}) is False
    assert _live({"domain": "x.lucidcove.org", "domain_live": True, "pending_host_command": ""}) is True
    assert _live({"domain": "x.lucidcove.org"}) is True  # existing Cove, no marker
