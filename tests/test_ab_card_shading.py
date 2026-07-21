"""Action Board social card shading — API vs paste, short vs long + legend."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "src/dashboard/static/js/action-board.js").read_text(encoding="utf-8")
CSS = (ROOT / "src/dashboard/static/css/action-board.css").read_text(encoding="utf-8")
AB_PY = (ROOT / "src/dashboard/routes/action_board.py").read_text(encoding="utf-8")


def test_js_defines_post_class_helper_and_legend():
    assert "function _cardPostClass" in JS
    assert "function _socialBoardLegendHtml" in JS
    assert "ab-post-api" in JS
    assert "ab-post-paste" in JS
    assert "ab-len-short" in JS
    assert "ab-len-long" in JS
    assert "showLegend" in JS


def test_css_shades_api_paste_short_long():
    assert ".ab-action-card.ab-post-api" in CSS
    assert ".ab-action-card.ab-post-paste" in CSS
    assert ".ab-action-card.ab-len-short" in CSS
    assert ".ab-action-card.ab-len-long" in CSS
    assert ".ab-board-legend" in CSS


def test_scheduled_api_exposes_post_mode_and_length():
    assert '"post_mode": "api"' in AB_PY or "'post_mode': \"api\"" in AB_PY or '"post_mode": "api"' in AB_PY
    assert "length_class" in AB_PY
    assert "post_mode" in AB_PY
