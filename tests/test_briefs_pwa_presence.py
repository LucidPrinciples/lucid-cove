"""Briefs PWA return path + presence-scoped library."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def briefs_tmp(tmp_path, monkeypatch):
    from src.dashboard.routes import briefs as br

    root = tmp_path / "briefs"
    monkeypatch.setattr(br, "BRIEFS_ROOT", root)
    monkeypatch.setattr(br, "BRIEFS_DOCS", root / "docs")
    monkeypatch.setattr(br, "BRIEFS_INDEX", root / "index.json")
    monkeypatch.setattr(br, "DATA_DIR", tmp_path)
    return br


def test_personal_library_is_own_plus_attached(briefs_tmp):
    br = briefs_tmp
    br.publish_doc(
        title="Teresa list",
        content_markdown="mine",
        presence_id="teresa-1",
        scope="presence",
    )
    br.publish_doc(
        title="Jason haven spec",
        content_markdown="ops",
        presence_id="jag-1",
        scope="presence",
    )
    br.publish_doc(
        title="Warehouse plan",
        content_markdown="shared job",
        presence_id="",
        scope="cove",
        project_slug="warehouse-liquidation",
    )
    br.publish_doc(
        title="Video pipeline plan",
        content_markdown="not attached",
        presence_id="",
        scope="cove",
        project_slug="video-pipeline",
    )
    br.publish_doc(
        title="Legacy unscoped",
        content_markdown="old",
    )
    data = br._load_index()
    for d in data["docs"]:
        if d.get("slug") == "legacy-unscoped":
            d["presence_id"] = ""
            d["scope"] = ""
    br._save_index(data)

    attached = {"warehouse-liquidation"}
    mine = {
        d["slug"]
        for d in br.list_docs(
            presence_id="teresa-1",
            full_cove=False,
            attached_project_slugs=attached,
        )
    }
    assert "teresa-list" in mine
    assert "warehouse-plan" in mine
    assert "jason-haven-spec" not in mine
    assert "video-pipeline-plan" not in mine
    assert "legacy-unscoped" not in mine
    other = next(d for d in br._load_index()["docs"] if d.get("slug") == "jason-haven-spec")
    assert not br.doc_visible_to(other, presence_id="teresa-1", full_cove=False)

    steward = {d["slug"] for d in br.list_docs(presence_id="jag-1", full_cove=True)}
    assert "jason-haven-spec" in steward
    assert "teresa-list" in steward
    assert "legacy-unscoped" in steward
    assert "video-pipeline-plan" in steward


def test_doc_visible_to_rules(briefs_tmp):
    personal = {"presence_id": "t1", "scope": "presence"}
    attached = {"presence_id": "", "scope": "cove", "project_slug": "ebay"}
    unattached = {"presence_id": "", "scope": "cove", "project_slug": "video-pipeline"}
    legacy = {"presence_id": "", "scope": ""}
    slugs = {"ebay"}
    assert briefs_tmp.doc_visible_to(personal, presence_id="t1", full_cove=False)
    assert not briefs_tmp.doc_visible_to(personal, presence_id="other", full_cove=False)
    assert briefs_tmp.doc_visible_to(
        attached,
        presence_id="t1",
        full_cove=False,
        attached_project_slugs=slugs,
    )
    assert not briefs_tmp.doc_visible_to(
        unattached,
        presence_id="t1",
        full_cove=False,
        attached_project_slugs=slugs,
    )
    assert not briefs_tmp.doc_visible_to(
        legacy, presence_id="t1", full_cove=False, for_library=True
    )
    assert briefs_tmp.doc_visible_to(
        legacy, presence_id="t1", full_cove=False, for_library=False
    )
    assert briefs_tmp.doc_visible_to(legacy, presence_id="t1", full_cove=True)
    assert not briefs_tmp.doc_visible_to(
        personal, presence_id="other", full_cove=False
    )


def test_publish_defaults_scope_from_presence(briefs_tmp):
    own = briefs_tmp.publish_doc(
        title="Phone nav",
        content_markdown="x",
        presence_id="beatrice-1",
    )
    assert own["presence_id"] == "beatrice-1"
    assert own["scope"] == "presence"
    shared = briefs_tmp.publish_doc(title="House note", content_markdown="y")
    assert shared["presence_id"] == ""
    assert shared["scope"] == "cove"


def test_reader_and_library_close_to_origin():
    reader = (ROOT / "src/dashboard/static/briefs/reader.html").read_text()
    library = (ROOT / "src/dashboard/static/briefs/library.html").read_text()
    for html in (reader, library):
        assert 'id="br-close"' in html
        assert "tab=ab-links" in html
        assert 'return "/?tab=chat"' in html
        assert "history.back()" not in html
    assert 'id="briefs-back"' in reader
    assert 'id="briefs-lib"' not in reader
    assert "← Briefs" in reader
    assert 'return "/briefs"' in reader
    assert 'encodeURIComponent("/briefs"' in library


def test_handle_door_does_not_inherit_admin_full_library():
    src = (ROOT / "src/dashboard/routes/briefs.py").read_text()
    assert 'kind == "cove"' in src
    assert "for_library=True" in src
    actor = src.split("async def _library_actor")[1].split("def publish_doc")[0]
    # Admin full catalog is Cove apex / manager only — not a handle door.
    assert "if kind == \"manager\":" in actor
    assert "if kind == \"cove\":" in actor
    assert actor.find("if kind == \"cove\":") < actor.find('if role in ("admin", "steward")')


def test_links_return_covers_brief_slug_and_same_window():
    js = (ROOT / "src/dashboard/static/js/action-board.js").read_text()
    assert "path.startsWith('/briefs/')" in js
    assert "sameApp" in js
    assert "Always new window/tab" not in js


def test_chat_briefs_stay_in_app_with_return_chat():
    msg = (ROOT / "src/dashboard/static/js/messaging.js").read_text()
    assert "u.searchParams.set('return', 'chat')" in msg
    assert "target=\"_blank\" rel=\"noopener\">' + path" not in msg
