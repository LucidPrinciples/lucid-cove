"""HISTFULL1 — History shell spans full width of Social Posts subpanel grid."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_history_full_width_css_and_shell():
    css = (ROOT / "src/dashboard/static/css/action-board.css").read_text()
    assert "HISTFULL1" in css
    assert "#ab-history-body" in css
    assert "grid-column: 1 / -1" in css
    assert "#ab-history-body .ab-session-body" in css
    assert "grid-template-columns: 1fr" in css
    js = (ROOT / "src/dashboard/static/js/action-board.js").read_text()
    assert 'id="ab-history-body"' in js
    assert "ab-history-body" in js
