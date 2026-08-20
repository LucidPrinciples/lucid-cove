"""Publish skips lanczos when the crop is already the deliverable size."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIDEO_PY = (ROOT / "voice" / "src" / "routes" / "video.py").read_text()


def _helpers():
    start = VIDEO_PY.index("def hq_scale(")
    end = VIDEO_PY.index("def scale_out_matrix")
    ns = {}
    exec(VIDEO_PY[start:end], ns)
    return ns


def test_hq_fit_skips_same_size():
    ns = _helpers()
    assert ns["hq_fit"](2160, 3840, 2160, 3840) == ""
    assert ns["hq_fit"](2160, 2160, 2160, 2160) == ""
    assert ns["hq_fit"](1620, 1620, 1620, 1620) == ""


def test_hq_fit_scales_when_crop_differs():
    ns = _helpers()
    up = ns["hq_fit"](1080, 1920, 2160, 3840)
    assert "scale=2160:3840" in up
    assert "lanczos" in up
    down = ns["hq_fit"](2700, 2700, 1620, 1620)
    assert "scale=1620:1620" in down


def test_process_moments_uses_hq_fit():
    proc = VIDEO_PY.split("async def process_moments", 1)[1].split(
        "async def caption_full_video", 1
    )[0]
    assert "hq_fit(" in proc
    # Fallback center-crop still scales (source square size unknown at graph build).
    assert "hq_scale(h_square, h_square" in proc


def test_caption_full_uses_hq_fit_on_operator_crop():
    cap = VIDEO_PY.split("async def caption_full_video", 1)[1]
    assert "hq_fit(" in cap
