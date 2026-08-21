"""process-moments: duration-scaled timeout + native HDR quality bar."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIDEO_PY = (ROOT / "voice" / "src" / "routes" / "video.py").read_text()
PIPE_PY = (ROOT / "src" / "dashboard" / "routes" / "video_pipeline.py").read_text()


def test_moment_encode_timeout_helper_present():
    assert "def moment_encode_timeout_seconds" in VIDEO_PY
    assert "native_hdr" in VIDEO_PY.split("def moment_encode_timeout_seconds", 1)[1][:400]


def test_moments_original_hdr_keeps_native_hlg():
    """Publish keeps camera HDR (10-bit HEVC HLG/PQ); does not flatten to Rec.709.

    Sidecar remakes (IMG_7129 / 7171 / 7131-7132) proved crop + ASS + per-clip
    sliders on keep-HDR. Stills still tonemap so the crop page matches <video>.
    """
    proc = VIDEO_PY.split("async def process_moments", 1)[1].split(
        "async def heal_inbox_processing", 1
    )[0]
    assert "native_hdr_encode_args(color_info)" in proc
    assert "KEEP HDR" in proc
    assert "no hable flatten" in proc
    assert "NATIVE COLOR PASSTHROUGH" not in proc
    assert "native_hdr_encode_args(color_info) if keep_hdr else None" in proc
    assert 'vf_prep = "" if native_v_args else color_prep_vf(color_info)' in proc
    assert '"-crf", "14"' in proc
    assert "libx265" in proc
    assert VIDEO_PY.count("native_hdr_encode_args(color_info) if keep_hdr else None") >= 2
    assert VIDEO_PY.count("KEEP HDR") >= 2
    assert "no native HLG re-tag" not in VIDEO_PY


def test_publish_scale_matrix_is_bt709():
    """SDR scale stays bt709; keep-HDR publish asks for bt2020, never bt2020nc."""
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
    assert ns["scale_out_matrix"](
        {"color_space": "bt2020nc"}, native_hdr=True
    ) == "bt2020"
    # Helper still knows how to ask for bt2020 if a future path needs it,
    # but zscale/x265 names must never leak into the scale filter.
    hdr = ns["hq_scale"](2160, 1620, out_matrix="bt2020")
    assert "out_color_matrix=bt2020" in hdr
    leaked = ns["hq_scale"](2160, 1620, out_matrix="bt2020nc")
    assert "out_color_matrix=bt2020" in leaked
    assert "bt2020nc" not in leaked
    # moments + caption-full pass native_hdr from keep-HDR encoder args
    assert "scale_out_matrix(color_info, native_hdr=bool(native_v_args))" in VIDEO_PY


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
