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


def test_original_is_true_identity():
    resolve, _ = _load_resolve()
    vf = resolve({"video_filter": "original"})
    assert vf == ""
    vf2 = resolve({
        "video_filter": "original",
        "filter_brightness": 0,
        "filter_contrast": 1,
        "filter_saturation": 1,
    })
    assert vf2 == ""
    # Slider nudge leaves identity path
    graded = resolve({
        "video_filter": "original",
        "filter_brightness": -0.05,
        "filter_contrast": 1.1,
        "filter_saturation": 1.0,
    })
    assert graded.startswith("eq=")
    assert "brightness=-0.05" in graded
    assert "curves" not in graded


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


def test_hq_scale_and_join_vf():
    text = VOICE_VIDEO.read_text()
    start = text.index("LOOK_PRESETS = {")
    end = text.index("def _square_crop_expr")
    ns = {}
    exec(text[start:end], ns)
    assert "lanczos" in ns["hq_scale"](2160, 1620)
    assert ns["join_vf"]("crop=1", "", None, "eq=x") == "crop=1,eq=x"
    assert ns["join_vf"]("crop=1", "") == "crop=1"


def test_encode_defaults_original_and_quality():
    text = VOICE_VIDEO.read_text()
    assert 'DEFAULT_VIDEO_FILTER = "original"' in text
    assert "flags=lanczos" in text
    assert "in_color_matrix=auto" in text
    # Near-transparent publish quality (was 16; still not a free re-mux)
    assert '"-crf", "14"' in text
    assert "colorprim=bt709" in text
    assert 'crop.get("video_filter", DEFAULT_VIDEO_FILTER)' in text
    # Prefer source fps over hard-coded 30
    assert "encode_fps_args" in text
    assert '"-r", "30"' not in text.split("def process_moments")[1].split("async def")[0]
    # identity path must not force eq onto original
    assert 'return ""' in text[text.index("def resolve_look_vf"):text.index("def hq_scale")]


def test_encode_fps_prefers_source_rate():
    text = VOICE_VIDEO.read_text()
    start = text.index("def probe_video_fps")
    end = text.index("def join_vf") if "def join_vf" in text[start:] else text.index("def _square_crop_expr")
    # load helpers that probe_video_fps needs
    chunk_start = text.index("def hq_scale")
    ns = {
        "subprocess": __import__("subprocess"),
        "logger": __import__("logging").getLogger("t"),
    }
    exec(text[chunk_start:text.index("def _square_crop_expr")], ns)
    assert ns["encode_fps_args"].__name__ == "encode_fps_args"
    # Simulated 60fps
    def fake_probe(path):
        return 59.94
    ns["probe_video_fps"] = fake_probe
    args = ns["encode_fps_args"]("/x.mov")
    assert args == ["-vsync", "cfr", "-r", "59.94"]


def test_crop_preview_identity_clears_css():
    # Media may be <video> or <img>; identity clears CSS filter on either
    assert "style.filter = 'none'" in CROP or 'style.filter = "none"' in CROP
    assert "identity — no color grade" in CROP
    assert "frameMediaEl" in CROP

