"""Project create + brief publish link plans onto project detail (modal)."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def briefs_env(tmp_path, monkeypatch):
    from src.dashboard.routes import briefs as br

    root = tmp_path / "briefs"
    root.mkdir()
    (root / "docs").mkdir()
    monkeypatch.setattr(br, "BRIEFS_ROOT", root)
    monkeypatch.setattr(br, "BRIEFS_DOCS", root / "docs")
    monkeypatch.setattr(br, "BRIEFS_INDEX", root / "index.json")
    br._ensure_dirs()
    return br


def test_publish_doc_stores_project_slug(briefs_env):
    br = briefs_env
    meta = br.publish_doc(
        title="Warehouse Liquidation plan",
        content_markdown="# Plan\n\nShip it.",
        kind="plan",
        summary="Sell the lot",
        project_slug="warehouse-liquidation",
    )
    assert meta["project_slug"] == "warehouse-liquidation"
    found = br.brief_for_project("warehouse-liquidation")
    assert found is not None
    assert found["slug"] == meta["slug"]
    assert found["kind"] == "plan"


def test_republish_same_project_updates_living_doc(briefs_env):
    br = briefs_env
    first = br.publish_doc(
        title="Demo plan",
        content_markdown="v1",
        kind="brief",
        project_slug="demo",
    )
    second = br.publish_doc(
        title="Demo plan v2",
        content_markdown="v2",
        kind="plan",
        project_slug="demo",
    )
    assert second["slug"] == first["slug"]
    linked = br.brief_for_project("demo")
    assert linked is not None
    assert linked["kind"] == "plan"
    assert linked["title"] == "Demo plan v2"



def test_publish_brief_tool_accepts_project_arg():
    src = (ROOT / "src/tools/briefs_tools.py").read_text()
    assert "project: str" in src
    assert "project_slug=" in src


def test_create_project_publishes_linked_plan():
    src = (ROOT / "src/tools/project_tools.py").read_text()
    assert "publish_doc" in src
    assert "project_slug=row" in src or 'project_slug=row["slug"]' in src
    assert "Plan brief" in src


def test_project_detail_api_includes_brief():
    src = (ROOT / "src/dashboard/routes/projects.py").read_text()
    assert "brief_for_project" in src
    assert '"brief": brief_meta' in src or "'brief': brief_meta" in src


def test_project_ui_has_brief_modal():
    panels = (ROOT / "src/dashboard/static/js/panels.js").read_text()
    projects = (ROOT / "src/dashboard/static/js/projects.js").read_text()
    css = (ROOT / "src/dashboard/static/css/components.css").read_text()
    assert "pdp-brief-card" in panels
    assert "openProjectBriefModal" in projects
    assert "project-brief-modal" in projects
    assert "project-brief-modal" in css
