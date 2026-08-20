"""Action cards: two-up desktop rows; title then Studio/badges."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "src/dashboard/static/css/action-board.css").read_text(encoding="utf-8")
JS = (ROOT / "src/dashboard/static/js/action-board.js").read_text(encoding="utf-8")


def test_desktop_two_column_action_grids():
    assert CSS.count("grid-template-columns: repeat(2, minmax(0, 1fr))") >= 2
    # Session groups (where most cards live) are two-up; History stays 1fr.
    marker = ".ab-session-body {\n    display: grid;\n    grid-template-columns: repeat(2, minmax(0, 1fr));"
    assert marker in CSS
    assert "#ab-history-body .ab-session-body" in CSS


def test_mobile_stacks_session_cards():
    mobile = CSS.split("@media (max-width: 768px)")[1]
    assert ".ab-session-body { grid-template-columns: 1fr; }" in mobile


def test_title_then_chrome_row():
    assert "ab-action-chrome" in CSS
    assert "ab-action-chrome" in JS
    assert "${platTag}${esc(s.title)}${ytLink}" not in JS
    assert 'class="ab-action-link"' in JS
    assert "Studio ↗" in JS
