"""#QL-EDIT / #QL-DRAG / #QL-SPACER + Action Links leaf+bundle cards.

Contract tests — source + schema. No browser driver required.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QL_JS = ROOT / "src/dashboard/static/js/quick-list.js"
QL_PY = ROOT / "src/dashboard/routes/quick_list.py"
QL_CSS = ROOT / "src/dashboard/static/css/dashboard.css"
AB_JS = ROOT / "src/dashboard/static/js/action-board.js"
AB_CSS = ROOT / "src/dashboard/static/css/action-board.css"
MIG = ROOT / "docker/migrations/039_quick_list_item_type.sql"


def test_migration_defines_item_type():
    sql = MIG.read_text()
    assert "item_type" in sql
    assert "spacer" in sql
    assert "ADD COLUMN" in sql


def test_api_accepts_spacer_and_returns_item_type():
    src = QL_PY.read_text()
    assert 'item_type' in src
    assert '"spacer"' in src or "'spacer'" in src
    assert "spacer_added" in src
    # counts exclude spacers
    assert "item_type" in src and "unchecked" in src
    assert "COALESCE(qli.item_type, 'item') = 'item'" in src


def test_ql_js_inline_edit():
    js = QL_JS.read_text()
    assert "function qlStartEdit" in js
    assert "ql-inline-input" in js
    assert "PATCH" in js
    assert "text:" in js or "text :" in js or '"text"' in js


def test_ql_js_drag_reorder():
    js = QL_JS.read_text()
    assert "function _qlDragStart" in js
    assert "function _qlDrop" in js
    assert "ql-drag-handle" in js
    assert "position" in js
    assert "draggable=\"true\"" in js
    # Mobile: HTML5 DnD is desktop-only — touch path on the handle
    assert "function _qlTouchStart" in js
    assert "function _qlTouchMove" in js
    assert "function _qlTouchEnd" in js
    assert "function _qlBindDragHandles" in js
    assert "function _qlReorderTo" in js
    assert "touchstart" in js
    assert "touchmove" in js
    assert "passive: false" in js or "passive:false" in js
    assert "elementFromPoint" in js


def test_ql_css_touch_drag():
    css = QL_CSS.read_text()
    assert "touch-action: none" in css
    # handle stays findable without hover on small screens
    assert ".ql-drag-handle" in css
    assert "min-width: 32px" in css or "min-height: 32px" in css


def test_ql_js_spacer():
    js = QL_JS.read_text()
    assert "function qlAddSpacer" in js
    assert "item_type" in js and "spacer" in js
    assert "ql-spacer-btn" in js
    assert "ql-item-spacer" in js


def test_ql_css_ux():
    css = QL_CSS.read_text()
    for needle in ("ql-drag-handle", "ql-inline-input", "ql-item-spacer", "ql-spacer-btn"):
        assert needle in css, needle


def test_links_leaf_and_bundle_contract():
    """Leaf tiles restored; bundles hold label+link rows (see test_links_bundles_and_backlog_pwa)."""
    css = AB_CSS.read_text()
    js = AB_JS.read_text()
    assert "a.ablk-card" in css
    assert "ablk-bundle" in css
    assert "function _abLinksRenderLeaf" in js
    assert "function _abLinksRenderBundle" in js
    assert 'target="_blank"' in js
    assert "ablk-label" in js and 'class="ablk-link"' in js
