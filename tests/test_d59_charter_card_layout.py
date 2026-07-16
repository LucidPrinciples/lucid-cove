"""#D59 — Charter card stacks mission + principles full-width.

Cosmetic UI: mission is a 2-row textarea (not a single-line input), both
fields override the global .settings-input max-width:220px so they no longer
render side-by-side / squished.
"""
from pathlib import Path
import re

JS = Path(__file__).resolve().parent.parent / "src/dashboard/static/js/settings-account.js"


def _charter_block() -> str:
    text = JS.read_text()
    m = re.search(r"const charterHtml = `(.*?)`;", text, re.S)
    assert m, "charterHtml template not found"
    return m.group(1)


def test_mission_is_textarea_two_rows():
    block = _charter_block()
    assert 'id="charter-mission"' in block
    assert re.search(r'<textarea[^>]*id="charter-mission"', block)
    assert re.search(r'id="charter-mission"[^>]*rows="2"', block) or re.search(
        r'rows="2"[^>]*id="charter-mission"', block
    )
    assert 'input type="text" id="charter-mission"' not in block
    assert '<input' not in block or 'charter-mission' not in re.search(
        r"<input[^>]*>", block + "<input>"
    ).group(0)


def test_fields_stack_full_width():
    block = _charter_block()
    assert "flex-direction:column" in block
    # both fields defeat the global max-width:220px on .settings-input
    assert block.count("max-width:none") >= 2
    assert block.count("width:100%") >= 2
    assert 'id="charter-principles"' in block


def test_save_load_hooks_unchanged():
    text = JS.read_text()
    assert "async function loadCharterCard" in text
    assert "async function saveCharter" in text
    assert "getElementById('charter-mission')" in text
    assert "getElementById('charter-principles')" in text
