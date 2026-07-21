"""Crop look presets + B/C/S slider overrides + live preview UI."""
from pathlib import Path
import importlib.util
import sys

ROOT = Path(__file__).resolve().parents[1]
VOICE_VIDEO = ROOT / "voice" / "src" / "routes" / "video.py"
CROP = (ROOT / "src" / "dashboard" / "static" / "action-board" / "video-crop-position.html").read_text()


def _load_resolve():
    """Load resolve_look_vf without importing full FastAPI voice app."""
    text = VOICE_VIDEO.read_text()
    # Exec only the LOOK_PRESETS + helpers block
    start = text.index("LOOK_PRESETS = {")
    end = text.index("def _square_crop_expr")
    ns = {}
    exec(text[start:end], ns)
    return ns["resolve_look_vf"], ns["LOOK_PRESETS"]


def test_original_is_identity_eq():
    resolve, _ = _load_resolve()
    vf = resolve({"video_filter": "original"})
    assert vf.startswith("eq=")
    assert "contrast=1" in vf
    assert "brightness=0" in vf
    assert "saturation=1" in vf
    assert "curves" not in vf
    assert "colortemperature" not in vf


def test_rich_keeps_curves_extra():
    resolve, _ = _load_resolve()
    vf = resolve({"video_filter": "rich"})
    assert "curves=" in vf
    assert "contrast=1.12" in vf


def test_slider_overrides_bcs():
    resolve, _ = _load_resolve()
    vf = resolve({
        "video_filter": "natural",
        "filter_brightness": 0.1,
        "filter_contrast": 1.25,
        "filter_saturation": 1.1,
    })
    assert "brightness=0.1" in vf
    assert "contrast=1.25" in vf
    assert "saturation=1.1" in vf
    assert "curves" not in vf  # natural has no extra


def test_cinematic_override_keeps_temp():
    resolve, _ = _load_resolve()
    vf = resolve({
        "video_filter": "cinematic",
        "filter_contrast": 1.3,
    })
    assert "contrast=1.3" in vf
    assert "colortemperature" in vf


def test_crop_ui_has_original_sliders_preview():
    assert "id: 'original'" in CROP or 'id: "original"' in CROP or "id: 'original'" in CROP
    assert "look-brightness" in CROP
    assert "look-contrast" in CROP
    assert "look-saturation" in CROP
    assert "applyLookPreview" in CROP
    assert "filter_brightness" in CROP
    assert "let videoFilter = 'original'" in CROP


def test_voice_uses_resolve_look_vf():
    text = VOICE_VIDEO.read_text()
    assert "VIDEO_FILTERS" not in text
    assert "resolve_look_vf(crop)" in text
    assert text.count("resolve_look_vf") >= 3
