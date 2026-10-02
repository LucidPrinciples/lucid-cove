"""PWA overlay close returns to the screen that opened the overlay."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_project_back_uses_origin_not_always_projects_list():
    js = (ROOT / "src/dashboard/static/js/projects.js").read_text()
    assert "_projectDetailOrigin" in js
    assert "_captureProjectOrigin" in js
    assert "switchToTab(origin)" in js
    assert "switchToTab('projects')" not in js.split("function backToProjects()")[1].split("function ")[0]


def test_nested_plan_and_table_links_carry_return_to_project():
    js = (ROOT / "src/dashboard/static/js/projects.js").read_text()
    assert "_mcReturnForProject" in js
    assert "searchParams.set('return'" in js
    assert 'target="_blank"' not in js.split("function openProjectBriefModal")[1].split("async function showProjectDetail")[0]


def test_home_calendar_team_open_project_without_forcing_projects_tab():
    cal = (ROOT / "src/dashboard/static/js/calendar.js").read_text()
    ov = (ROOT / "src/dashboard/static/js/overview.js").read_text()
    team = (ROOT / "src/dashboard/static/js/team.js").read_text()
    core = (ROOT / "src/dashboard/static/js/core.js").read_text()
    assert "openProjectDetail(" in cal
    assert "switchTab('projects');setTimeout(()=>showProjectDetail" not in cal
    assert "openProjectDetail(" in ov
    assert "openProjectDetail(" in team
    assert "async function openProjectDetail" in core
    assert "function switchTab(tabName)" in core
    assert "_bootParams.get('project')" in core


def test_table_viewer_close_honors_return_not_briefs_library():
    html = (ROOT / "src/dashboard/static/tables/viewer.html").read_text()
    assert 'id="tables-back"' in html
    assert "returnTarget" in html
    assert 'href="/briefs"' not in html
    assert "history.back()" not in html


def test_brief_reader_accepts_project_return_path():
    html = (ROOT / "src/dashboard/static/briefs/reader.html").read_text()
    assert 'return "/?tab=projects"' in html
    assert "project=" in html
    assert "history.back()" not in html
