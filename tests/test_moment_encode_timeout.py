"""process-moments: duration-scaled timeout + native HDR quality bar."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIDEO_PY = (ROOT / "voice" / "src" / "routes" / "video.py").read_text()
PIPE_PY = (ROOT / "src" / "dashboard" / "routes" / "video_pipeline.py").read_text()


def test_moment_encode_timeout_helper_present():
    assert "def moment_encode_timeout_seconds" in VIDEO_PY
    assert "native_hdr" in VIDEO_PY.split("def moment_encode_timeout_seconds", 1)[1][:400]


def test_moments_original_hdr_uses_display_sdr_not_native_retag():
    """Publish must tonemap HDR→SDR; never clear vf_prep for a native HLG re-tag.

    2026-07-22 P620 QA: crop/scale/ASS + libx265 tagged HLG read red/sparkly
    next to the camera file. Original look still means no eq grade — color_prep
    (gamut-map before tonemap) stays on.
    """
    proc = VIDEO_PY.split("async def process_moments", 1)[1].split(
        "async def heal_inbox_processing", 1
    )[0]
    assert "native_v_args = None" in proc
    assert "DISPLAY SDR" in proc
    assert "NATIVE COLOR PASSTHROUGH" not in proc
    # Must not disable color_prep on the moments path anymore
    assert 'vf_prep = ""' not in proc
    assert "color_prep_vf(color_info)" in proc
    # SDR publish quality bar still CRF 14
    assert '"-crf", "14"' in proc
    assert "colorprim=bt709" in proc
    # moments + caption-full both force the display-SDR path
    assert VIDEO_PY.count("native_v_args = None") >= 2
    assert VIDEO_PY.count("DISPLAY SDR") >= 2
    assert "no native HLG re-tag" in VIDEO_PY


def test_publish_scale_matrix_is_bt709():
    """After display-SDR switch, publish scale must not request bt2020 matrix."""
    assert "def scale_out_matrix" in VIDEO_PY
    assert "out_matrix=scale_matrix" in VIDEO_PY
    start = VIDEO_PY.index("LOOK_PRESETS = {")
    end = VIDEO_PY.index("def _square_crop_expr")
    ns = {}
    exec(VIDEO_PY[start:end], ns)
    sdr = ns["hq_scale"](2160, 1620)
    assert "out_color_matrix=bt709" in sdr
    assert ns["scale_out_matrix"](
        {"color_space": "bt2020nc"}, native_hdr=False
    ) == "bt709"
    # Helper still knows how to ask for bt2020 if a future path needs it,
    # but zscale/x265 names must never leak into the scale filter.
    hdr = ns["hq_scale"](2160, 1620, out_matrix="bt2020")
    assert "out_color_matrix=bt2020" in hdr
    leaked = ns["hq_scale"](2160, 1620, out_matrix="bt2020nc")
    assert "out_color_matrix=bt2020" in leaked
    assert "bt2020nc" not in leaked
    # moments + caption-full force native_hdr=False on scale_out_matrix
    assert "scale_out_matrix(color_info, native_hdr=False)" in VIDEO_PY


def test_moments_timeout_is_dynamic_not_fixed_600():
    proc = VIDEO_PY.split("async def process_moments", 1)[1].split(
        "async def heal_inbox_processing", 1
    )[0]
    assert "moment_encode_timeout_seconds" in proc
    assert "timeout=600)" not in proc.replace(" ", "")
    assert "Timed out after" in proc


def test_read_json_endpoint_exists():
    assert '@router.get("/api/video/read-json")' in VIDEO_PY
    assert "async def read_json" in VIDEO_PY


def test_app_reads_json_via_voice():
    fn = PIPE_PY.split("async def _read_video_json", 1)[1].split(
        "\ndef _parse_propfind", 1
    )[0]
    assert "/api/video/read-json" in fn
    # voice before raw NC dav fallback
    assert fn.index("read-json") < fn.index("remote.php/dav/files")
