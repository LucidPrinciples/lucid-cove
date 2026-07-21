"""Post-transcribe: originals leave inbox for processing (no dual stuck)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIFE = (ROOT / "voice/src/video_lifecycle.py").read_text(encoding="utf-8")
STT = (ROOT / "voice/src/routes/stt.py").read_text(encoding="utf-8")
VOICE = (ROOT / "voice/src/routes/video.py").read_text(encoding="utf-8")
PIPE = (ROOT / "src/dashboard/routes/video_pipeline.py").read_text(encoding="utf-8")
UI = (ROOT / "src/dashboard/static/action-board/full-video-pipeline.html").read_text(
    encoding="utf-8"
)
COMMON = (ROOT / "voice/src/voice_common.py").read_text(encoding="utf-8")


def test_lifecycle_heal_helper_exists():
    assert "async def ensure_inbox_cleared_after_processing" in LIFE
    assert "nc_delete_inbox_dual" in LIFE or "delete_inbox" in LIFE
    assert "min_size_ratio" in LIFE


def test_stt_calls_heal_after_transcript():
    assert "ensure_inbox_cleared_after_processing" in STT
    assert "lifecycle_heal" in STT


def test_heal_routes_app_and_voice():
    assert "/heal-inbox-processing" in PIPE
    assert "/api/video/heal-inbox-processing" in VOICE


def test_pipeline_ui_lists_processing_folder():
    assert "/api/video/processing" in UI
    assert "folder: 'processing'" in UI
    assert "folder: 'inbox'" in UI


def test_ncsession_file_meta_exists():
    assert "async def file_meta" in COMMON
    assert "async def exists" in COMMON
