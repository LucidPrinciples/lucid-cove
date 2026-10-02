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


def test_personal_library_hides_other_presence_and_legacy(briefs_tmp):
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
        title="Shared how briefs work",
        content_markdown="shared",
        presence_id="",
        scope="cove",
    )
    br.publish_doc(
        title="Legacy unscoped",
        content_markdown="old",
    )
    # Force the last one to the pre-scope shape (empty presence + empty scope)
    data = br._load_index()
    for d in data["docs"]:
        if d.get("slug") == "legacy-unscoped":
            d["presence_id"] = ""
            d["scope"] = ""
    br._save_index(data)

    mine = {d["slug"] for d in br.list_docs(presence_id="teresa-1", full_cove=False)}
    assert "teresa-list" in mine
    assert "shared-how-briefs-work" in mine
    assert "jason-haven-spec" not in mine
    assert "legacy-unscoped" not in mine

    steward = {d["slug"] for d in br.list_docs(presence_id="jag-1", full_cove=True)}
    assert "jason-haven-spec" in steward
    assert "teresa-list" in steward
    assert "legacy-unscoped" in steward


def test_doc_visible_to_rules(briefs_tmp):
    personal = {"presence_id": "t1", "scope": "presence"}
    cove = {"presence_id": "", "scope": "cove"}
    legacy = {"presence_id": "", "scope": ""}
    assert briefs_tmp.doc_visible_to(personal, presence_id="t1", full_cove=False)
    assert not briefs_tmp.doc_visible_to(personal, presence_id="other", full_cove=False)
    assert briefs_tmp.doc_visible_to(cove, presence_id="t1", full_cove=False)
    assert not briefs_tmp.doc_visible_to(legacy, presence_id="t1", full_cove=False)
    assert briefs_tmp.doc_visible_to(legacy, presence_id="t1", full_cove=True)


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
        assert "return=links" in html or "tab=ab-links" in html
        assert 'return "/?tab=chat"' in html
        assert "display-mode: standalone" in html
    assert 'id="briefs-lib"' in reader
    assert "/briefs" in reader and "keep" in reader


def test_links_return_covers_brief_slug_and_same_window():
    js = (ROOT / "src/dashboard/static/js/action-board.js").read_text()
    assert "path.startsWith('/briefs/')" in js
    assert "sameApp" in js
    assert "Always new window/tab" not in js


def test_chat_briefs_stay_in_app_with_return_chat():
    msg = (ROOT / "src/dashboard/static/js/messaging.js").read_text()
    assert "u.searchParams.set('return', 'chat')" in msg
    assert "target=\"_blank\" rel=\"noopener\">' + path" not in msg
