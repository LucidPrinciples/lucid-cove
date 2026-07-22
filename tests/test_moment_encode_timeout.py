"""process-moments: duration-scaled timeout + fast native preset for shorts."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIDEO_PY = (ROOT / "voice" / "src" / "routes" / "video.py").read_text()
PIPE_PY = (ROOT / "src" / "dashboard" / "routes" / "video_pipeline.py").read_text()


def test_moment_encode_timeout_helper_present():
    assert "def moment_encode_timeout_seconds" in VIDEO_PY
    assert "native_hdr" in VIDEO_PY.split("def moment_encode_timeout_seconds", 1)[1][:400]


def test_moments_use_fast_native_preset():
    # Shorts path must request fast; caption-full keeps default medium
    assert 'native_hdr_encode_args(color_info, preset="fast")' in VIDEO_PY
    assert "NATIVE COLOR PASSTHROUGH (10-bit HEVC fast" in VIDEO_PY


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
