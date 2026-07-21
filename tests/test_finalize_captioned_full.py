"""Finalize captioned full: rename harden + finalize endpoint + crop UI."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIDEO_PY = (ROOT / "voice" / "src" / "routes" / "video.py").read_text()
COMMON = (ROOT / "voice" / "src" / "voice_common.py").read_text()
PROC = (ROOT / "src" / "dashboard" / "routes" / "video_processing.py").read_text()
CROP = (ROOT / "src" / "dashboard" / "static" / "action-board" / "video-crop-position.html").read_text()


def test_rename_fixes_root_ownership_and_move_timeout():
    assert "docker cp recovery" in VIDEO_PY or "_fix_mount_perms" in VIDEO_PY
    assert "timeout=600.0" in VIDEO_PY or "timeout=600" in VIDEO_PY
    assert "timeout: float = 120.0" in COMMON
    assert "AsyncClient(timeout=timeout)" in COMMON


def test_finalize_endpoint_exists():
    assert "/finalize-captioned-full" in PROC
    assert "_finalize_captioned_full_metadata" in PROC
    # caption-full success path uses shared helper
    assert "await _finalize_captioned_full_metadata" in PROC


def test_crop_ui_finalize_button():
    assert "btn-finalize-full" in CROP
    assert "finalizeCaptionedFull" in CROP
    assert "/api/video/finalize-captioned-full" in CROP
