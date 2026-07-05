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
