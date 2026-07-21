"""Crop page: native video preview, stable border layout, usable seek scrubber."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CROP = (ROOT / "src/dashboard/static/action-board/video-crop-position.html").read_text(
    encoding="utf-8"
)
VOICE = (ROOT / "voice/src/routes/video.py").read_text(encoding="utf-8")


def test_crop_prefers_native_video_stream_not_only_jpeg():
    assert "frame-video" in CROP
    assert "/api/video/proxy/raw" in CROP
    assert "streamUrlFor" in CROP
    # Still path remains as poster/fallback
    assert "/api/video/proxy/frame" in CROP
    assert "onFrameVideoError" in CROP


def test_border_toggle_does_not_resize_crop_window():
    # The old bug stretched video-window to FRAME_H and top=0 when border off.
    assert "vw.style.height = FRAME_H" not in CROP
    assert "vw.style.top = '0'" not in CROP or "border" not in CROP
    # Stable square placement
    assert "vw.style.top = BAR_TOP + 'px'" in CROP
    assert "vw.style.height = SQUARE_SIZE + 'px'" in CROP
    assert "videoWindowHeight()" in CROP
    assert "return SQUARE_SIZE" in CROP


def test_seek_scrubber_not_floored_to_zero_range():
    # floor(duration) pegged short/unknown clips at max=0
    assert "Math.floor(videoInfo.duration)" not in CROP
    assert "seekDuration" in CROP
    assert "seek-slider" in CROP
    assert "currentTime" in CROP


def test_apply_look_targets_video_or_img():
    assert "frameMediaEl()" in CROP
    assert "applyLookPreview" in CROP
    # Must not only look up frame-img
    idx = CROP.index("function applyLookPreview")
    chunk = CROP[idx : idx + 400]
    assert "frameMediaEl()" in chunk


def test_video_info_duration_guards_na():
    assert 'val == "N/A"' in VOICE or "N/A" in VOICE
    assert "_dur" in VOICE or "duration =" in VOICE
