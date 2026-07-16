"""Door host must match session Presence (Matthews install-pass).

Team page pairings stay correct (DB). MC URL can show a different Presence's
header when the shared cookie rides onto another handle subdomain. host_match
already knew; / must redirect to the session owner's door.
"""
from pathlib import Path

APP = Path("src/dashboard/app.py").read_text()
HOST = Path("src/dashboard/host_context.py").read_text()
AGENTS = Path("src/dashboard/routes/agents.py").read_text()
TEAM = Path("src/dashboard/static/js/team.js").read_text()
ONBOARD = Path("src/dashboard/routes/onboarding.py").read_text()


def test_host_match_compares_handle_to_username():
    assert "kind == \"handle\"" in HOST or "kind == 'handle'" in HOST
    assert "username" in HOST
    assert "ctx.get(\"label\")" in HOST or "ctx.get('label')" in HOST


def test_root_redirects_mismatched_handle_door():
    assert "Door/session mismatch" in APP
    assert "host_match(_hc_door, account)" in APP
    assert "kind\") == \"handle\"" in APP or "kind'] == 'handle'" in APP
    assert "subdomain_routing" in APP
    # Redirects to session owner's own handle door, not landing
    assert "_own}.{_dom}" in APP or "{_own}.{_dom}" in APP


def test_presences_expose_mc_url_from_handle():
    assert "Live MC door per Presence" in AGENTS
    assert '"mc_url": _mc_url' in AGENTS or "'mc_url': _mc_url" in AGENTS
    assert "{_handle}.{_mc_domain}" in AGENTS


def test_team_open_mc_uses_member_mc_url():
    assert "member.mc_url" in TEAM
    assert "Open MC" in TEAM


def test_founder_gate_covers_admin_provisioned_coadmin():
    # Still on this branch — earliest admin, not just invite row
    assert "ORDER BY created_at ASC" in ONBOARD
    assert "presence_invites WHERE consumed_by" in ONBOARD
