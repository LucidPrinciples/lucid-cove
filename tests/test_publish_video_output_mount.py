"""publish_video_output prefers NC data mount; RO mount falls back to WebDAV."""
import asyncio
import importlib.util
import os
from pathlib import Path

_MOD_PATH = Path(__file__).resolve().parents[1] / "voice" / "src" / "voice_common.py"


def _load_vc(monkeypatch, **env):
    for k, v in env.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    spec = importlib.util.spec_from_file_location(
        f"voice_common_publish_{os.getpid()}_{id(env)}", _MOD_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_publish_prefers_nc_data_mount_over_webdav(tmp_path, monkeypatch):
    html = tmp_path / "html"
    user = "jason"
    shorts = (
        html / "data" / user / "files" / "AgentSkills" / "Content" / "video" / "shorts"
    )
    shorts.mkdir(parents=True)
    src = tmp_path / "out.mp4"
    src.write_bytes(b"captioned-full-bytes")

    vc = _load_vc(monkeypatch, NC_HTML_ROOT=str(html), NEXTCLOUD_USER=user)
    # Avoid docker.sock NC scan noise in unit tests
    monkeypatch.setattr(vc, "_nc_scan", lambda *a, **k: None)

    class _NC:
        pushed = False

        def __init__(self, nc_user):
            self.user = nc_user

        async def push(self, subpath, local_path, content_type="application/octet-stream"):
            self.pushed = True
            return False

    nc = _NC(user)
    ok = asyncio.get_event_loop().run_until_complete(
        vc.publish_video_output(str(src), "shorts/IMG_7171-captioned.mp4", nc, "video/mp4")
    )
    assert ok is True
    assert nc.pushed is False
    dest = shorts / "IMG_7171-captioned.mp4"
    assert dest.is_file()
    assert dest.read_bytes() == b"captioned-full-bytes"


def test_publish_ro_mount_falls_back_to_webdav(tmp_path, monkeypatch):
    html = tmp_path / "html"
    # Directory exists but is not writable → OSError on copy/makedirs path.
    # Simulate by pointing NC_HTML_ROOT at a file so makedirs/copy fails.
    bogus = tmp_path / "not-a-dir"
    bogus.write_text("x")
    src = tmp_path / "out.mp4"
    src.write_bytes(b"x" * 64)

    vc = _load_vc(monkeypatch, NC_HTML_ROOT=str(bogus), NEXTCLOUD_USER="jason")
    monkeypatch.setattr(vc, "_nc_scan", lambda *a, **k: None)

    class _NC:
        user = "jason"
        pushed = False

        async def push(self, subpath, local_path, content_type="application/octet-stream"):
            self.pushed = True
            assert subpath == "shorts/big.mp4"
            assert Path(local_path).read_bytes() == b"x" * 64
            return True

    nc = _NC()
    ok = asyncio.get_event_loop().run_until_complete(
        vc.publish_video_output(str(src), "shorts/big.mp4", nc, "video/mp4")
    )
    assert ok is True
    assert nc.pushed is True


def test_publish_webdav_false_when_mount_missing_and_push_fails(tmp_path, monkeypatch):
    src = tmp_path / "out.mp4"
    src.write_bytes(b"data")
    vc = _load_vc(monkeypatch, NC_HTML_ROOT="", NEXTCLOUD_USER="jason")

    class _NC:
        user = "jason"

        async def push(self, subpath, local_path, content_type="application/octet-stream"):
            return False

    ok = asyncio.get_event_loop().run_until_complete(
        vc.publish_video_output(str(src), "shorts/x.mp4", _NC(), "video/mp4")
    )
    assert ok is False


def test_caption_full_checks_publish_return(monkeypatch):
    """Regression: caption-full must not 200/graduate when publish returns False."""
    video_py = (
        Path(__file__).resolve().parents[1] / "voice" / "src" / "routes" / "video.py"
    ).read_text()
    assert "wrote = await publish_video_output" in video_py
    assert "Captioned full publish FAILED" in video_py
    assert 'status_code=502' in video_py
    # Graduate only after success path (still present, but after the wrote gate)
    fail_idx = video_py.index("Captioned full publish FAILED")
    grad_idx = video_py.index("graduate_processing_to_raw", fail_idx)
    assert grad_idx > fail_idx
