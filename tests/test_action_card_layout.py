"""Action cards: double Links-tile width, pack across the monitor; title then Studio/badges."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "src/dashboard/static/css/action-board.css").read_text(encoding="utf-8")
JS = (ROOT / "src/dashboard/static/js/action-board.js").read_text(encoding="utf-8")


def test_desktop_double_tile_action_grids():
    assert CSS.count("grid-template-columns: repeat(auto-fill, minmax(440px, 1fr))") >= 3
    marker = ".ab-session-body {\n    display: grid;\n    grid-template-columns: repeat(auto-fill, minmax(440px, 1fr));"
    assert marker in CSS
    # History uses the same packing — no 1fr override on session bodies.
    assert "#ab-history-body .ab-session-body" not in CSS


def test_mobile_stacks_session_cards():
    mobile = CSS.split("@media (max-width: 768px)")[1]
    assert ".ab-session-body { grid-template-columns: 1fr; }" in mobile


def test_title_then_chrome_row():
    assert "ab-action-chrome" in CSS
    assert "ab-action-chrome" in JS
    assert "${platTag}${esc(s.title)}${ytLink}" not in JS
    assert 'class="ab-action-link"' in JS
    assert "Studio ↗" in JS
