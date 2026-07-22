"""process-moments: duration-scaled timeout + native HDR quality bar."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIDEO_PY = (ROOT / "voice" / "src" / "routes" / "video.py").read_text()
PIPE_PY = (ROOT / "src" / "dashboard" / "routes" / "video_pipeline.py").read_text()


def test_moment_encode_timeout_helper_present():
    assert "def moment_encode_timeout_seconds" in VIDEO_PY
    assert "native_hdr" in VIDEO_PY.split("def moment_encode_timeout_seconds", 1)[1][:400]


def test_moments_use_quality_native_preset():
    # Shorts path: medium + CRF 14 (not fast/18). Duration-scaled timeout owns wall clock.
    assert 'native_hdr_encode_args(color_info, preset="medium")' in VIDEO_PY
    assert "NATIVE COLOR PASSTHROUGH (10-bit HEVC medium/crf14" in VIDEO_PY
    native_fn = VIDEO_PY.split("def native_hdr_encode_args", 1)[1].split(
        "def moment_encode_timeout_seconds", 1
    )[0]
    assert '"-crf", "14"' in native_fn
    assert '"-crf", "18"' not in native_fn


def test_native_hdr_scale_keeps_bt2020_matrix():
    assert "def scale_out_matrix" in VIDEO_PY
    assert "out_matrix=scale_matrix" in VIDEO_PY
    # Default SDR scale still bt709; native path must be able to request bt2020nc
    start = VIDEO_PY.index("LOOK_PRESETS = {")
    end = VIDEO_PY.index("def _square_crop_expr")
    ns = {}
    exec(VIDEO_PY[start:end], ns)
    sdr = ns["hq_scale"](2160, 1620)
    assert "out_color_matrix=bt709" in sdr
    hdr = ns["hq_scale"](2160, 1620, out_matrix="bt2020nc")
    assert "out_color_matrix=bt2020nc" in hdr
    assert ns["scale_out_matrix"](
        {"color_space": "bt2020nc"}, native_hdr=True
    ) == "bt2020nc"
    assert ns["scale_out_matrix"]({}, native_hdr=False) == "bt709"


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
