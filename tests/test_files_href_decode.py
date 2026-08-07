"""Files list must unquote WebDAV href so parent is skipped (no nested self 404)."""

from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]


def test_files_list_decodes_href_before_parent_skip():
    src = (ROOT / "src/dashboard/routes/files.py").read_text()
    assert "unquote(href)" in src
    assert "parent_rel" in src
    assert "item_path" in src


def test_files_js_uses_item_path():
    js = (ROOT / "src/dashboard/static/js/files.js").read_text()
    assert "item.path" in js
    assert "currentFilePath.replace(/\\/$/, )}/${item.name}" not in js


def test_unquote_parent_skip_logic():
    nc_user = "jeffreydavid"
    base_path = "/remote.php/dav/files/" + nc_user
    clean_path = "Ebay Store"
    parent_rel = clean_path.strip("/")
    href = base_path + "/Ebay%20Store/"
    href_dec = unquote(href)
    rel = href_dec.split(base_path, 1)[-1].strip("/")
    assert rel == parent_rel
    href2 = base_path + "/Ebay%20Store/notes.txt"
    rel2 = unquote(href2).split(base_path, 1)[-1].strip("/")
    assert rel2 != parent_rel
    assert rel2 == "Ebay Store/notes.txt"
