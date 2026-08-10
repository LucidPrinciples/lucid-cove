"""Download pack helpers + Files API surface (HTTPS bulk pull)."""
import asyncio
import zipfile
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_pack_name_excluded_preview_and_junk():
    from src.dashboard.routes.files_pack import pack_name_excluded, filter_pack_items

    assert pack_name_excluded("clip-preview.mp4") is True
    assert pack_name_excluded("IMG_7159-m1-quote-partnership-preview.mp4") is True
    assert pack_name_excluded("clip-captioned.mp4") is False
    assert pack_name_excluded("clip.mp4") is False
    assert pack_name_excluded(".DS_Store") is True

    items = [
        {"name": "a.mp4", "path": "shorts/a.mp4", "is_dir": False, "size": 10},
        {"name": "a-preview.mp4", "path": "shorts/a-preview.mp4", "is_dir": False, "size": 1},
        {"name": "sub", "path": "shorts/sub", "is_dir": True, "size": 0},
        {"name": "b-captioned.mp4", "path": "shorts/b-captioned.mp4", "is_dir": False, "size": 20},
    ]
    kept = filter_pack_items(items, exclude_preview=True)
    names = [k["name"] for k in kept]
    assert names == ["a.mp4", "b-captioned.mp4"]
    kept_all = filter_pack_items(items, exclude_preview=False)
    assert any("preview" in k["name"] for k in kept_all)


def test_iter_zip_stored_roundtrip():
    from src.dashboard.routes.files_pack import iter_zip_stored

    async def body_a():
        yield b"hello "
        yield b"world"

    async def body_b():
        yield b"second"

    async def collect():
        chunks = []
        async for c in iter_zip_stored(
            [("folder/a.txt", body_a()), ("b.txt", body_b())]
        ):
            chunks.append(c)
        return b"".join(chunks)

    data = asyncio.run(collect())
    zf = zipfile.ZipFile(BytesIO(data))
    assert sorted(zf.namelist()) == ["b.txt", "folder/a.txt"]
    assert zf.read("folder/a.txt") == b"hello world"
    assert zf.read("b.txt") == b"second"


def test_files_py_has_pack_routes_and_streaming_download():
    src = (ROOT / "src/dashboard/routes/files.py").read_text()
    assert '@router.get("/api/files/pack")' in src
    assert '@router.post("/api/files/pack/zip")' in src
    assert "aiter_bytes" in src
    assert "iter([response.content])" not in src
    assert "stream=True" in src


def test_files_js_pack_ui_and_no_blob_bulk():
    js = (ROOT / "src/dashboard/static/js/files.js").read_text()
    assert "openDownloadPack" in js
    assert "packDownloadToFolder" in js
    assert "/api/files/pack" in js
    assert "showDirectoryPicker" in js
    # Sequential bulk must not buffer whole files via res.blob()
    chunk = js.split("async function packFetchToDisk")[1].split("async function packDownloadToFolder")[0]
    assert "await res.blob()" not in chunk


def test_panels_has_download_pack_button():
    panels = (ROOT / "src/dashboard/static/js/panels.js").read_text()
    assert "openDownloadPack" in panels
    assert "download-pack-panel" in panels
