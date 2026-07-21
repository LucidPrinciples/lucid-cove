"""Local ASR must not send voice host-only NC_PIPECAT stamps (crop info 404)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIPE = (ROOT / "src/dashboard/routes/video_pipeline.py").read_text(encoding="utf-8")
CROP = (ROOT / "src/dashboard/static/action-board/video-crop-position.html").read_text(
    encoding="utf-8"
)


def test_local_mode_ignores_host_docker_internal_nc_pipecat():
    idx = PIPE.index("async def pipecat_nc_headers")
    chunk = PIPE[idx : idx + 3500]
    assert "_is_host_only" in chunk
    assert "host.docker.internal" in chunk
    # Local path prefers NEXTCLOUD_URL
    assert 'env("NEXTCLOUD_URL")' in chunk
    # Must not unconditionally prefer explicit over NEXTCLOUD_URL without host-only filter
    assert "if _is_host_only(explicit)" in chunk or "_is_host_only(explicit) and" in chunk


def test_headers_fall_back_to_get_nc_creds():
    idx = PIPE.index("async def pipecat_nc_headers")
    chunk = PIPE[idx : idx + 3500]
    assert "get_nc_creds" in chunk


def test_crop_resolves_filename_from_inbox_list():
    assert "resolveSourceFilename" in CROP
    assert "/api/video/inbox" in CROP
    assert "/api/video/processing" in CROP
    assert "encodeURIComponent(fname)" in CROP


def test_processing_list_route_exists():
    assert '@router.get("/processing")' in PIPE
