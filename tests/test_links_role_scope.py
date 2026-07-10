# #D29 — scope the Links/Action board by role. A manager MC (steward/merchant,
# host_context.kind === "manager") shows its LANE resources (queue, repos,
# runbooks) and NOT the per-Presence intake surfaces (Backlog + Jules). Presences
# and the operator/admin keep Backlog/Jules/Cloud.
import pathlib

LINKS = (pathlib.Path(__file__).resolve().parents[1]
         / "src" / "dashboard" / "static" / "action-board" / "links.html").read_text()


def test_intake_rows_are_tagged():
    # Backlog + Jules rows carry the presence-intake scope so they can be hidden
    # for a manager
    # two markup rows carry the scope (a third occurrence is the JS selector)
    assert LINKS.count('data-scope="presence-intake"><span class="link-label">') == 2
    seg = LINKS.split('id="my-tools-links"')[1][:600]
    assert 'Backlog' in seg and 'Jules' in seg
    assert 'data-scope="presence-intake"' in seg


def test_manager_lane_section_exists_with_lane_resources():
    assert 'data-role="manager"' in LINKS
    assert 'id="manager-lane"' in LINKS
    lane = LINKS.split('id="manager-lane"')[1][:900]
    # queue, repos, runbooks — the lane resources
    assert 'Queue' in lane
    assert 'Repos' in lane
    assert 'Runbooks' in lane


def test_viewer_kind_read_from_host_context():
    assert "config.host_context && config.host_context.kind" in LINKS
    assert "viewerKind === 'manager'" in LINKS


def test_apply_role_hides_intake_for_manager():
    # the presence-intake rows are display:none exactly when manager
    assert "querySelectorAll('[data-scope=\"presence-intake\"]')" in LINKS
    assert "isManager ? 'none' : ''" in LINKS


def test_apply_role_shows_manager_lane_only_for_manager():
    assert "querySelectorAll('[data-role=\"manager\"]')" in LINKS
    assert "isManager ? '' : 'none'" in LINKS


def test_admin_sections_stay_admin_only():
    # a manager is NOT the Cove admin — admin sections still gate on isAdmin
    assert "querySelectorAll('[data-role=\"admin\"]')" in LINKS
    assert "isAdmin ? '' : 'none'" in LINKS


def test_manager_gets_a_jump_bar_entry():
    assert "href: '#manager-lane'" in LINKS
