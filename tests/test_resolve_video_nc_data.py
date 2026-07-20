"""NC_HTML_ROOT local resolve prefers mounted NC data over WebDAV pull."""
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
    # Fresh module each time so module-level env reads re-evaluate
    spec = importlib.util.spec_from_file_location(
        f"voice_common_under_test_{os.getpid()}_{id(env)}", _MOD_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_find_on_nc_data_and_resolve_prefers_mount(tmp_path, monkeypatch):
    html = tmp_path / "html"
    user = "jason"
    rel = Path("data") / user / "files" / "AgentSkills" / "Content" / "video" / "inbox"
    (html / rel).mkdir(parents=True)
    target = html / rel / "IMG_7171.MOV"
    target.write_bytes(b"fake-video-bytes")

    vc = _load_vc(
        monkeypatch,
        NC_HTML_ROOT=str(html),
        VIDEO_SCRATCH=str(tmp_path / "scratch"),
    )

    assert vc.find_on_nc_data(user, "IMG_7171.MOV").endswith("IMG_7171.MOV")

    class _NC:
        def __init__(self):
            self.user = user
            self.pulled = False

        async def pull(self, subpath, local_path):
            self.pulled = True
            return False

    nc = _NC()
    path = asyncio.get_event_loop().run_until_complete(
        vc.resolve_video_source("IMG_7171.MOV", nc)
    )
    assert path == str(target)
    assert nc.pulled is False


def test_resolve_without_mount_falls_through_to_pull(tmp_path, monkeypatch):
    vc = _load_vc(
        monkeypatch,
        NC_HTML_ROOT="",
        VIDEO_SCRATCH=str(tmp_path / "scratch"),
    )

    class _NC:
        user = "jason"

        async def pull(self, subpath, local_path):
            Path(local_path).parent.mkdir(parents=True, exist_ok=True)
            Path(local_path).write_bytes(b"pulled")
            return True

    path = asyncio.get_event_loop().run_until_complete(
        vc.resolve_video_source("x.MOV", _NC())
    )
    assert path and path.endswith("x.MOV")
    assert Path(path).read_bytes() == b"pulled"
