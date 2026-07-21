"""X scheduled posts appear on Scheduled tab; crop look syntax fix."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AB = (ROOT / "src" / "dashboard" / "routes" / "action_board.py").read_text()
JS = (ROOT / "src" / "dashboard" / "static" / "js" / "action-board.js").read_text()
CROP = (ROOT / "src" / "dashboard" / "static" / "action-board" / "video-crop-position.html").read_text()


def test_crop_no_stray_const_comment():
    assert "const //" not in CROP
    assert "const VIDEO_FILTER_PRESETS" in CROP


def test_scheduled_includes_x_social_queue():
    assert "platform = 'x'" in AB
    assert 'source": "social_queue"' in AB
    assert 'source": "youtube_queue"' in AB


def test_publish_now_kicks_x_process_queue():
    assert "/api/x/process-queue" in JS
    assert "publishSocialNow" in JS
    assert "status: 'queued'" in JS


def test_scheduled_cards_platform_aware():
    assert "openSocialDetail" in JS
    assert "plat === 'x'" in JS
