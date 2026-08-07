"""Briefs reader: publish, promote, render, chat deep-link surface."""

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


def test_publish_and_read(briefs_tmp):
    br = briefs_tmp
    meta = br.publish_doc(
        title="Door Scope Fix",
        content_markdown="# Door Scope\n\n## Why\n\nAdmin role alone was wrong.\n\n- manager door\n- personal attach\n",
        kind="brief",
        summary="Scope projects by MC door.",
        published_by="stuart",
    )
    assert meta["slug"] == "door-scope-fix"
    assert meta["kind"] == "brief"
    path = br._doc_path(meta["slug"])
    assert path.is_file()
    body = br._read_body(meta)
    assert "Admin role alone" in body
    html = br._render_html(body)
    assert "<h1>" in html and "<h2>" in html and "<li>" in html

    listed = br.list_docs()
    assert any(d["slug"] == "door-scope-fix" for d in listed)

    # republish same title updates in place
    meta2 = br.publish_doc(
        title="Door Scope Fix",
        content_markdown="# Door Scope\n\nUpdated body.\n",
        kind="brief",
        summary="updated",
    )
    assert meta2["slug"] == meta["slug"]
    assert "Updated body" in br._read_body(meta2)


def test_promote_brief_to_plan_to_spec(briefs_tmp):
    br = briefs_tmp
    meta = br.publish_doc(
        title="Mesh Invite",
        content_markdown="## Outline\n\nWalk members to mesh.\n",
        kind="brief",
    )
    m2, err = br.promote_doc(meta["slug"], "plan")
    assert not err and m2["kind"] == "plan"
    m3, err = br.promote_doc("Mesh Invite", "spec")
    assert not err and m3["kind"] == "spec"
    # demote blocked
    bad, err = br.promote_doc(meta["slug"], "brief")
    assert bad is None and "demote" in err.lower()


def test_slugify_and_url(briefs_tmp):
    br = briefs_tmp
    assert br._slugify("Hello, World!") == "hello-world"
    assert br.reader_url("hello-world") == "/briefs/hello-world"


def test_vault_path_traversal_blocked(briefs_tmp, tmp_path, monkeypatch):
    br = briefs_tmp
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setattr(br, "VAULT_ROOT", vault)
    assert br._safe_vault_path("../etc/passwd") is None
    assert br._safe_vault_path("/etc/passwd") is None
    good = vault / "AgentSkills" / "Working" / "note.md"
    good.parent.mkdir(parents=True)
    good.write_text("# Hi\n", encoding="utf-8")
    got = br._safe_vault_path("AgentSkills/Working/note.md")
    assert got is not None and got.read_text(encoding="utf-8").startswith("# Hi")


def test_static_surfaces_and_wiring():
    reader = (ROOT / "src/dashboard/static/briefs/reader.html").read_text()
    library = (ROOT / "src/dashboard/static/briefs/library.html").read_text()
    assert "prose" in reader and "/api/briefs/" in reader
    assert "Brief → Plan → Spec" in library or "Brief" in library
    app = (ROOT / "src/dashboard/app.py").read_text()
    assert "briefs" in app
    tools = (ROOT / "src/tools/briefs_tools.py").read_text()
    assert "publish_brief" in tools and "promote_brief" in tools
    agent = (ROOT / "src/tools/agent_tools.py").read_text()
    assert "ALL_BRIEFS_TOOLS" in agent
    msg = (ROOT / "src/dashboard/static/js/messaging.js").read_text()
    assert "/briefs" in msg and "formatMessage" in msg
    # Trailing punctuation must not ride into href (chat often wraps (/briefs/slug).)
    assert "while (path && /[.,);:!?]$/.test(path))" in msg
    assert "trail = path.slice(-1) + trail" in msg
    links = (ROOT / "src/dashboard/static/action-board/links.html").read_text()
    assert 'href="/briefs"' in links


def test_tools_export_names():
    from src.tools.briefs_tools import ALL_BRIEFS_TOOLS

    names = {t.name for t in ALL_BRIEFS_TOOLS}
    assert names == {"publish_brief", "promote_brief", "list_briefs"}
