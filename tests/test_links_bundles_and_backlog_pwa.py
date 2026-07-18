"""Links leaf restore + bundles + backlog PWA / close affordance.

Contract tests — source + sanitize. No browser driver required.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AB_JS = ROOT / "src/dashboard/static/js/action-board.js"
AB_CSS = ROOT / "src/dashboard/static/css/action-board.css"
BL_HTML = ROOT / "src/dashboard/static/backlog.html"
BL_PY = ROOT / "src/dashboard/routes/backlog.py"
JL_HTML = ROOT / "src/dashboard/static/jules.html"


def _load_sanitize():
    import sys
    sys.path.insert(0, str(ROOT))
    from src.dashboard.routes import action_board as ab
    return ab._sanitize_links


def test_leaf_card_restored():
    js = AB_JS.read_text()
    css = AB_CSS.read_text()
    assert "function _abLinksRenderLeaf" in js
    assert 'class="ablk-card"' in js
    assert 'target="_blank"' in js
    assert "function _abLinksRenderBundle" in js
    assert "ablk-bundle" in js and "ablk-bundle" in css
    assert "auto-fill" in css and "minmax(220px" in css
    assert "a.ablk-card" in css
    assert "ablk-card-t" in css and "ablk-card-n" in css


def test_bundle_rows_label_and_link():
    js = AB_JS.read_text()
    css = AB_CSS.read_text()
    assert "ablk-label" in js and "ablk-link" in js
    assert "ablk-label" in css and "a.ablk-link" in css
    assert "spacer" in js and "subhead" in js
    assert "abLinksToggleBundle" in js
    assert "ablk-collapsed" in css
    assert "ablk-wide" in css


def test_sanitize_bundle_and_leaf():
    sanitize = _load_sanitize()
    out = sanitize({
        "cards": [
            {"type": "link", "title": "Backlog", "url": "/backlog", "note": "x", "icon": "X"},
            {
                "type": "bundle",
                "title": "Monthly Bills",
                "wide": True,
                "items": [
                    {"kind": "row", "label": "1st -", "text": "SBA", "url": "https://example.com/sba"},
                    {"kind": "spacer"},
                    {"kind": "subhead", "label": "Account 1"},
                    {"kind": "row", "label": "Dashboard", "text": "dash.cloudflare.com", "url": "javascript:alert(1)"},
                    {"kind": "row", "label": "ok", "text": "site", "url": "example.com/x"},
                ],
            },
            {"type": "bundle", "title": "", "items": []},
        ]
    })
    cards = out["cards"]
    assert len(cards) == 2
    leaf = cards[0]
    assert leaf["type"] == "link" and leaf["url"] == "/backlog"
    b = cards[1]
    assert b["type"] == "bundle" and b["title"] == "Monthly Bills" and b["wide"] is True
    kinds = [i["kind"] for i in b["items"]]
    assert kinds == ["row", "spacer", "subhead", "row", "row"]
    assert b["items"][3]["url"] == ""
    assert b["items"][4]["url"].startswith("https://")


def test_backlog_pwa_meta():
    html = BL_HTML.read_text()
    assert 'apple-mobile-web-app-title" content="Backlog"' in html
    assert "backlog-manifest.webmanifest" in html
    assert "apple-touch-icon" in html
    assert 'id="bl-close"' in html
    py = BL_PY.read_text()
    assert "backlog-manifest.webmanifest" in py
    assert '"short_name": "Backlog"' in py
    assert '"start_url": "/backlog"' in py


def test_jules_and_backlog_close_affordance():
    bl = BL_HTML.read_text()
    jl = JL_HTML.read_text()
    assert "bl-show-close" in bl and "history.back" in bl
    assert "jl-show-close" in jl and "history.back" in jl
    # × must honor ?return=links → Action Links, not bare history.back
    assert "returnTarget" in bl and "/?tab=ab-links" in bl
    assert "returnTarget" in jl and "/?tab=ab-links" in jl
    js = AB_JS.read_text()
    assert "function _abLinksWithReturn" in js
    assert "return', 'links'" in js or 'return", "links"' in js or "set('return', 'links')" in js
    core = (ROOT / "src/dashboard/static/js/core.js").read_text()
    assert "_abTabIds" in core
    assert "switchBoard('action')" in core
