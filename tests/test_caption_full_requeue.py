"""Caption-full re-queue: mount-first exists/skip/rename; idempotent social_queue."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIDEO_PY = (ROOT / "voice" / "src" / "routes" / "video.py").read_text()
PROC_PY = (ROOT / "src" / "dashboard" / "routes" / "video_processing.py").read_text()


def test_caption_full_exists_prefers_mount():
    assert "find_on_nc_data(nc.user, plain" in VIDEO_PY
    # pull remains last resort, after mount/scratch checks
    exists_fn = VIDEO_PY.split("async def caption_full_exists", 1)[1].split(
        "async def ", 1
    )[0]
    assert "find_on_nc_data" in exists_fn
    assert exists_fn.index("find_on_nc_data") < exists_fn.index("await nc.pull")


def test_caption_full_skip_returns_duration():
    guard = VIDEO_PY.split("Guard: skip if captioned full", 1)[1].split(
        "Find source video", 1
    )[0]
    assert "duration_seconds" in guard
    assert "find_on_nc_data" in guard
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
