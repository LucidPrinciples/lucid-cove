"""Action Board — per-platform Recent (last 3 days) vs All drafts."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "src/dashboard/static/js/action-board.js").read_text(encoding="utf-8")
AB_PY = (ROOT / "src/dashboard/routes/action_board.py").read_text(encoding="utf-8")


def test_js_has_per_platform_recent_lane():
    assert "const _AB_RECENT_MS = 3 * 24 * 60 * 60 * 1000" in JS
    assert "function _abGetPlatformLane" in JS
    assert "function _renderPlatformCards" in JS
    assert "function _abSetPlatformLane" in JS
    assert "_AB_PLATFORM_SUBS" in JS
    assert "youtube-short" in JS
    assert "tiktok" in JS
    assert "x-post" in JS
    assert "instagram" in JS
    assert "facebook" in JS
    assert "_renderPlatformCards(sub.items, sub.id)" in JS
    assert "sort: 'history'" in JS
    assert "}, opts);" in JS


def test_queue_cards_expose_created_at():
    assert '"created_at":' in AB_PY or "'created_at':" in AB_PY
    assert "row[\"created_at\"].isoformat()" in AB_PY
