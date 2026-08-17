"""Install dogfood from 2026-08-17 jules — names, reach card, invite tunnel, Knowledge-off-tab."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_offline_name_bank_tops_up_to_eight():
    from src.dashboard.routes import agent_provision as ap

    names = ap._offline_agent_names("The Guide", "neutral", ["elara", "silas"])
    assert len(names) >= 6
    blocked = {"elara", "silas", "stuart", "vera"}
    assert not {n["name"].lower() for n in names} & blocked

    # Simulate a spark list that reserved-name filter emptied to four.
    short = [{"name": n} for n in ("Cedric", "Maren", "Solace", "Quill")]
    have = {n["name"].lower() for n in short}
    extra = ap._offline_agent_names("The Witness", "neutral", list(have | {"stuart", "atlas"}))
    for item in extra:
        if item["name"].lower() not in have:
            short.append(item)
            have.add(item["name"].lower())
        if len(short) >= 8:
            break
    assert len(short) >= 8


def test_knowledge_is_not_an_mc_tab():
    cfg = (ROOT / "src/config.py").read_text()
    assert "tabs = [t for t in tabs if" in cfg
    assert '"id": "knowledge"' not in cfg.split("Knowledge (Ezra)")[1][:400]
    core = (ROOT / "src/dashboard/static/js/core.js").read_text()
    assert "if (tabId === 'team') names.push('tuning')" in core


def test_invite_by_link_keeps_tunnel_until_confirmed():
    reach = (ROOT / "src/dashboard/routes/reachability.py").read_text()
    assert "/api/reachability/public/confirm" in reach
    assert '"mode": "tunnel"' in reach
    team = (ROOT / "src/dashboard/static/js/team.js").read_text()
    assert "loadInviteTunnelStep" in team
    assert "off-mesh" in team
    assert "I ran it — off-mesh join works" in team
    settings = (ROOT / "src/dashboard/static/js/settings-account.js").read_text()
    assert "Make this Cove public" not in settings
    assert "Invite by Link" in settings


def test_new_cove_setup_hidden_after_name():
    ab = (ROOT / "src/dashboard/static/js/action-board.js").read_text()
    assert "does not install another Cove" in ab
    assert "new cove" in ab.lower()
    assert "family_name" in ab


def test_hard_to_reach_leads_with_next_action():
    home = (ROOT / "src/dashboard/static/js/home.js").read_text()
    onboard = (ROOT / "src/dashboard/routes/onboarding.py").read_text()
    assert "Next: run the command" in home
    assert "Your Cove still works over the relay" in onboard
    assert "enable UPnP/NAT-PMP" in onboard
