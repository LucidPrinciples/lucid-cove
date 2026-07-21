"""Crop page: native video preview, border full-9:16, usable seek scrubber."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CROP = (ROOT / "src/dashboard/static/action-board/video-crop-position.html").read_text(
    encoding="utf-8"
)
VOICE = (ROOT / "voice/src/routes/video.py").read_text(encoding="utf-8")
PIPELINE = (ROOT / "src/dashboard/routes/video_pipeline.py").read_text(encoding="utf-8")


def test_crop_prefers_native_video_stream_not_only_jpeg():
    assert "frame-video" in CROP
    assert "/api/video/proxy/raw" in CROP
    assert "streamUrlFor" in CROP
    # Still path remains as poster/fallback
    assert "/api/video/proxy/frame" in CROP
    assert "onFrameVideoError" in CROP


def test_border_off_fills_916_and_keeps_captions():
    """Border off = full 9:16 video fill; captions stay; blacks go transparent/away."""
    assert "videoWindowHeight()" in CROP
    assert "borderEnabled ? SQUARE_SIZE : FRAME_H" in CROP
    # Full-frame path when border off
    assert "vw.style.top = '0'" in CROP
    assert "vw.style.height = FRAME_H + 'px'" in CROP
    # Captions remain visible
    assert "cap.style.visibility = 'visible'" in CROP
    assert "botBar.style.background = 'transparent'" in CROP
    # Border on still uses square placement
    assert "vw.style.top = BAR_TOP + 'px'" in CROP
    assert "vw.style.height = SQUARE_SIZE + 'px'" in CROP


def test_border_off_encode_uses_rect_crop_not_square():
    assert "def _rect_crop_expr" in VOICE
    assert "_rect_crop_expr(src_w, src_h, src_x, src_y)" in VOICE
    # The no-border vertical branch must not square-crop
    idx = VOICE.index("Vertical without border")
    chunk = VOICE[idx : idx + 280]
    assert "_rect_crop_expr" in chunk
    assert "_square_crop_expr" not in chunk


def test_proxy_raw_mov_is_quicktime_not_mp4():
    # Hardcoding video/mp4 for every founder FileResponse washed/broke MOV preview
    idx = PIPELINE.index("async def proxy_video_raw")
    end = PIPELINE.index('@router.get("/proxy/frame")')
    chunk = PIPELINE[idx:end]
    assert "video/quicktime" in chunk
    assert 'media_type="video/mp4"' not in chunk or "_raw_types" in chunk


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
