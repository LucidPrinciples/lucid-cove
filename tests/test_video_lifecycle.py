"""batch8 #1 / C4 — processing → raw graduation. Best-effort, never raises.

The module lives in voice/src (a separate src root from cove-core), so it's loaded
by file path to avoid the `src` namespace collision.
"""
import asyncio
import importlib.util
import os
from pathlib import Path

import pytest

_MOD_PATH = Path(__file__).resolve().parent.parent / "voice" / "src" / "video_lifecycle.py"
_spec = importlib.util.spec_from_file_location("voice_video_lifecycle", _MOD_PATH)
lifecycle = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lifecycle)


class _FakeNC:
    def __init__(self, present):  # present = the src subpath that "exists"
        self.present = present
        self.moves = []

    async def move(self, src, dst):
        self.moves.append((src, dst))
        return src == self.present


def test_local_mount_graduates(tmp_path):
    proc = tmp_path / "processing"
    proc.mkdir()
    (proc / "IMG_7129.MOV").write_text("video-bytes")
    ok = asyncio.run(lifecycle.graduate_processing_to_raw(
        "IMG_7129", nc=None, video_mount=str(tmp_path)))
    assert ok is True
    assert (tmp_path / "raw" / "IMG_7129.MOV").exists()
    assert not (proc / "IMG_7129.MOV").exists()  # MOVED, not copied


def test_local_mount_hyphenated_stem(tmp_path):
    proc = tmp_path / "processing"
    proc.mkdir()
    (proc / "IMG_7168-Test1.mp4").write_text("v")
    ok = asyncio.run(lifecycle.graduate_processing_to_raw(
        "IMG_7168-Test1", nc=None, video_mount=str(tmp_path)))
    assert ok is True
    assert (tmp_path / "raw" / "IMG_7168-Test1.mp4").exists()


def test_local_mount_missing_original_is_noop(tmp_path):
    (tmp_path / "processing").mkdir()
    ok = asyncio.run(lifecycle.graduate_processing_to_raw(
        "NOPE", nc=None, video_mount=str(tmp_path)))
    assert ok is False  # nothing to graduate, no error


def test_nc_path_moves_processing_to_raw():
    nc = _FakeNC(present="processing/IMG_7129.MOV")
    ok = asyncio.run(lifecycle.graduate_processing_to_raw("IMG_7129", nc=nc))
    assert ok is True
    assert ("processing/IMG_7129.MOV", "raw/IMG_7129.MOV") in nc.moves


def test_nc_path_no_original_returns_false():
    nc = _FakeNC(present="processing/OTHER.MOV")
    ok = asyncio.run(lifecycle.graduate_processing_to_raw("IMG_7129", nc=nc))
    assert ok is False


def test_never_raises_on_error():
    class _Boom:
        async def move(self, *a):
            raise RuntimeError("network gone")
    ok = asyncio.run(lifecycle.graduate_processing_to_raw("x", nc=_Boom()))
    assert ok is False  # swallowed, render never fails


def test_retire_local_moves_not_deletes(tmp_path):
    shorts = tmp_path / "shorts"
    shorts.mkdir()
    f = shorts / "clip.mp4"
    f.write_text("clip-bytes")
    result = asyncio.run(lifecycle.retire_to_delete(
        "shorts/clip.mp4", nc=None, video_mount=str(tmp_path)))
    assert result["ok"] is True
    assert result["method"] == "local_move"
    assert not f.exists()
    dest = tmp_path / result["dest"]
    assert dest.exists()
    assert dest.read_text() == "clip-bytes"
    assert str(result["dest"]).startswith("to-delete/")


def test_retire_nc_uses_move():
    nc = _FakeNC(present="shorts/clip.mp4")
    # FakeNC.move returns True only when src == present
    result = asyncio.run(lifecycle.retire_to_delete("shorts/clip.mp4", nc=nc))
    assert result["ok"] is True
    assert result["method"] == "nc_move"
    assert nc.moves and nc.moves[0][0] == "shorts/clip.mp4"
    assert nc.moves[0][1].startswith("to-delete/")


def test_retire_never_raises():
    class _Boom:
        async def move(self, *a):
            raise RuntimeError("gone")
        async def delete(self, *a):
            raise RuntimeError("gone")
    result = asyncio.run(lifecycle.retire_to_delete("x", nc=_Boom()))
    assert result["ok"] is False


def test_to_delete_total_bytes(tmp_path):
    d = tmp_path / "to-delete"
    d.mkdir()
    (d / "a.bin").write_bytes(b"12345")
    (d / "b.bin").write_bytes(b"abc")
    assert lifecycle.to_delete_total_bytes(str(tmp_path)) == 8


def test_delete_file_route_no_os_remove():
    """Lock: /api/video/delete-file must not os.remove user content."""
    src = (Path(__file__).resolve().parents[1] / "voice/src/routes/video.py").read_text()
    # Isolate the delete-file handler body
    start = src.find('@router.post("/api/video/delete-file")')
    assert start > 0
    end = src.find("\n@router.", start + 10)
    block = src[start:end if end > 0 else start + 2000]
    assert "retire_to_delete" in block
    assert "os.remove(" not in block  # allow docstring mention


def test_stt_prefers_move_no_inbox_delete_after_copy():
    src = (Path(__file__).resolve().parents[1] / "voice/src/routes/stt.py").read_text()
    assert "nc.move(f\"inbox/{video_name}\"" in src or 'nc.move(f"inbox/{video_name}"' in src
    # The old post-copy inbox delete must be gone
    assert 'await nc.delete(f"inbox/{video_name}")' not in src
    assert "inbox original kept" in src or "WebDAV MOVE inbox" in src


def test_files_delete_moves_to_to_delete():
    src = (Path(__file__).resolve().parents[1] / "src/dashboard/routes/files.py").read_text()
    start = src.find('@router.delete("/api/files/delete")')
    block = src[start:start + 2500]
    assert "AgentSkills/To-Delete" in block
    assert 'request("MOVE"' in block or "MOVE" in block


def test_to_delete_notify_default_is_100_gib():
    """Video originals are multi-GiB; 5 GiB was noise. Default must stay high."""
    root = Path(__file__).resolve().parents[1]
    sched = (root / "src/utils/scheduler.py").read_text()
    assert "100 * 1024 ** 3" in sched
    assert "TO_DELETE_NOTIFY_BYTES" in sched
    env_src = (root / "src/env.py").read_text()
    assert "TO_DELETE_NOTIFY_BYTES" in env_src
    assert "100 * 1024 ** 3" in env_src


def test_inbox_seed_documents_jules_hot_path_vs_video_sync():
    """Seeded READMEs teach selective sync so Jules stays off the video queue."""
    src = (Path(__file__).resolve().parents[1]
           / "src/dashboard/routes/nextcloud.py").read_text()
    assert "Jules hot path" in src or "jules hot path" in src.lower()
    assert "selective sync" in src.lower() or "selective-sync" in src.lower()
    assert "AgentSkills/Content/video/README.md" in src
    assert "100 GiB" in src
