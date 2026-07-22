"""Caption-full re-queue: mount-first exists/skip/rename; idempotent social_queue."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIDEO_PY = (ROOT / "voice" / "src" / "routes" / "video.py").read_text()
PROC_PY = (ROOT / "src" / "dashboard" / "routes" / "video_processing.py").read_text()


def test_caption_full_exists_prefers_mount():
    exists_fn = VIDEO_PY.split("async def caption_full_exists", 1)[1].split(
        "async def ", 1
    )[0]
    assert "_resolve_captioned_full" in exists_fn
    helper = VIDEO_PY.split("async def _resolve_captioned_full", 1)[1].split(
        "@router.get", 1
    )[0]
    # mount before pull
    assert "find_on_nc_data" in helper or "_find_captioned_full_on_mount" in VIDEO_PY
    assert helper.index("_find_captioned_full_on_mount") < helper.index("await nc.pull")


def test_caption_full_skip_returns_duration():
    guard = VIDEO_PY.split("Guard: skip if captioned full", 1)[1].split(
        "Find source video", 1
    )[0]
    assert "duration_seconds" in guard
    assert "_resolve_captioned_full" in guard
    assert "ffprobe" in guard


def test_rename_captioned_uses_move_not_pull_push():
    rename = VIDEO_PY.split("async def rename_captioned", 1)[1].split(
        "async def caption_full_video", 1
    )[0]
    assert "await nc.move" in rename
    assert "Renamed captioned on mount" in rename
    # old pull+push path must not be the primary rename
    assert "await nc.push(f\"shorts/{new_name}\"" not in rename


def test_queue_full_is_idempotent():
    assert "queued_platforms" in PROC_PY
    assert ":update" in PROC_PY
    assert "SELECT id, status FROM social_queue" in PROC_PY
    assert "_finalize_captioned_full_metadata" in PROC_PY


def test_caption_full_exists_sees_title_renamed():
    """After rename-captioned, exists must find STEM-*-captioned.mp4 not only plain."""
    helper = VIDEO_PY.split("async def _resolve_captioned_full", 1)[1].split(
        "@router.get", 1
    )[0]
    assert "*-captioned.mp4" in helper
    assert "_find_captioned_full_on_mount" in VIDEO_PY
    mount_fn = VIDEO_PY.split("def _find_captioned_full_on_mount", 1)[1].split(
        "async def _resolve_captioned_full", 1
    )[0]
    assert "*-captioned.mp4" in mount_fn


def test_caption_full_skip_uses_resolve_helper():
    guard = VIDEO_PY.split("Guard: skip if captioned full", 1)[1].split(
        "Find source video", 1
    )[0]
    assert "_resolve_captioned_full" in guard
    assert "duration_seconds" in guard
