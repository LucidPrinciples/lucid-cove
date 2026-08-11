"""Download pack helpers + Files API surface (HTTPS bulk pull for every Cove)."""
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


def test_sort_pack_items_newest_first():
    from src.dashboard.routes.files_pack import sort_pack_items_newest_first, parse_modified_ts

    assert parse_modified_ts("Mon, 10 Aug 2026 18:00:00 GMT") > parse_modified_ts(
        "Sun, 09 Aug 2026 18:00:00 GMT"
    )
    items = [
        {"name": "old.mp4", "modified": "Sun, 09 Aug 2026 12:00:00 GMT"},
        {"name": "new.mp4", "modified": "Mon, 10 Aug 2026 18:00:00 GMT"},
        {"name": "mid.mp4", "modified": "Mon, 10 Aug 2026 09:00:00 GMT"},
        {"name": "unknown.mp4", "modified": ""},
    ]
    ordered = sort_pack_items_newest_first(items)
    assert [x["name"] for x in ordered] == ["new.mp4", "mid.mp4", "old.mp4", "unknown.mp4"]


def test_pack_progress_key():
    from src.dashboard.routes.files_pack import pack_progress_key

    assert pack_progress_key("AgentSkills/Content/video/shorts") == "AgentSkills/Content/video/shorts"
    assert pack_progress_key("/a/b/") == "a/b"
    assert pack_progress_key("") == "root"


def test_parse_ocs_share_payload_json_and_xml():
    from src.dashboard.routes.files import _parse_ocs_share_payload

    j = {
        "ocs": {
            "meta": {"statuscode": 200},
            "data": {"id": 9, "token": "abcTOKEN", "url": "https://cloud.example/s/abcTOKEN", "share_type": 3},
        }
    }
    meta = _parse_ocs_share_payload(j)
    assert meta.get("token") == "abcTOKEN"
    assert meta.get("statuscode") == 200

    listed = {
        "ocs": {
            "meta": {"statuscode": 200},
            "data": [
                {"share_type": 0, "token": ""},
                {"share_type": 3, "token": "pub1", "url": "https://cloud.example/s/pub1"},
            ],
        }
    }
    meta2 = _parse_ocs_share_payload(listed)
    assert isinstance(meta2.get("_list"), list)
    assert meta2["_list"][1]["token"] == "pub1"

    xml = """<?xml version="1.0"?><ocs><meta><statuscode>100</statuscode></meta>
    <data><token>xmlTok</token><url>https://cloud.example/s/xmlTok</url><share_type>3</share_type></data></ocs>"""
    meta3 = _parse_ocs_share_payload(xml)
    assert meta3.get("token") == "xmlTok"
    assert meta3.get("statuscode") == 100


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
    assert '@router.post("/api/files/pack/direct-urls")' in src
    assert '@router.get("/api/files/pack/progress")' in src
    assert "aiter_bytes" in src
    assert "iter([response.content])" not in src
    assert "stream=True" in src
    assert "sort_pack_items_newest_first" in src
    assert "_mint_public_file_share" in src
    assert "shareType" in src


def test_files_js_pack_ui_newest_resume_and_direct_cloud():
    js = (ROOT / "src/dashboard/static/js/files.js").read_text()
    assert "openDownloadPack" in js
    assert "packDownloadDirectCloud" in js
    assert "packSelectNewerThanLast" in js
    assert "packMarkDoneThrough" in js
    assert "/api/files/pack/direct-urls" in js
    assert "newest first" in js
    assert "localStorage" in js
    assert "showDirectoryPicker" in js
    # Sequential bulk must not buffer whole files via res.blob() in the click path
    chunk = js.split("async function packFetchToDisk")[1].split("async function packDownloadToFolder")[0]
    assert "await res.blob()" not in chunk
    # Primary download is Cloud-direct
    assert "packDownloadDirectCloud" in js
    assert "packTriggerBrowserDownload" in js
    assert "Download next only" in js
    assert "one-at-a-time" in js
    assert "Via Mission Control" in js
    assert "download shelf" in js or "Downloads folder" in js


def test_panels_has_download_pack_button():
    panels = (ROOT / "src/dashboard/static/js/panels.js").read_text()
    assert "openDownloadPack" in panels
    assert "download-pack-panel" in panels


def test_cloud_public_url_explicit_wins_over_domain(monkeypatch):
    """Self-host bulk egress: NEXTCLOUD_PUBLIC_URL must beat derived cloud.{domain}."""
    import src.config as cfg

    monkeypatch.setenv("NEXTCLOUD_PUBLIC_URL", "https://files.example.org")
    monkeypatch.setattr(cfg, "load_cove_config", lambda: {"domain": "smith.example.org"})
    assert cfg._cloud_public_url() == "https://files.example.org"
    assert cfg.get_nc_public_url() == "https://files.example.org"


def test_cloud_public_url_rejects_loopback_explicit(monkeypatch):
    import src.config as cfg

    monkeypatch.setenv("NEXTCLOUD_PUBLIC_URL", "http://127.0.0.1:8082")
    monkeypatch.setattr(cfg, "load_cove_config", lambda: {"domain": "smith.example.org"})
    assert cfg._cloud_public_url() == "https://cloud.smith.example.org"


def test_cloud_public_url_domain_when_no_explicit(monkeypatch):
    import src.config as cfg

    monkeypatch.delenv("NEXTCLOUD_PUBLIC_URL", raising=False)
    monkeypatch.setattr(cfg, "load_cove_config", lambda: {"domain": "smith.example.org"})
    assert cfg._cloud_public_url() == "https://cloud.smith.example.org"


def test_is_browser_public_origin_helpers():
    from src.config import _is_browser_public_origin

    assert _is_browser_public_origin("https://files.example.org") is True
    assert _is_browser_public_origin("http://127.0.0.1:8082") is False
    assert _is_browser_public_origin("http://localhost") is False
    assert _is_browser_public_origin("http://nextcloud") is False
    assert _is_browser_public_origin("") is False


def test_ensure_public_hostname_merges_without_wipe():
    """Pure merge logic: catch-all stays last; other hostnames preserved."""
    # Inline the merge algorithm used by ensure_public_hostname (no CF API).
    existing = [
        {"hostname": "analytics.example.org", "service": "http://127.0.0.1:8303"},
        {"hostname": "old.example.org", "service": "http://127.0.0.1:9"},
        {"service": "http_status:404"},
    ]
    hostname = "files.example.org"
    new_rule = {"hostname": hostname, "service": "http://127.0.0.1:8082"}
    catch_all, kept, replaced = [], [], False
    for rule in existing:
        if not (rule.get("hostname") or "").strip():
            catch_all.append(rule)
            continue
        if (rule.get("hostname") or "").strip().rstrip(".").lower() == hostname:
            kept.append(new_rule)
            replaced = True
        else:
            kept.append(rule)
    if not replaced:
        kept.append(new_rule)
    if not catch_all:
        catch_all = [{"service": "http_status:404"}]
    ingress = kept + catch_all
    hosts = [r.get("hostname") for r in ingress if r.get("hostname")]
    assert hosts == ["analytics.example.org", "old.example.org", "files.example.org"]
    assert ingress[-1]["service"] == "http_status:404"
    assert replaced is False


def test_files_py_reports_egress_and_public_base_contract():
    src = (ROOT / "src/dashboard/routes/files.py").read_text()
    assert '"egress"' in src or "'egress'" in src
    assert "missing_public_base" in src
    assert "_public_cloud_base" in src


def test_enable_public_cloud_script_exists():
    p = ROOT / "provision/enable_public_cloud.py"
    assert p.is_file()
    text = p.read_text()
    assert "ensure_public_hostname" in text
    assert "NEXTCLOUD_PUBLIC_URL" in text
    assert "never paste" in text.lower() or "do not paste" in text.lower()


def test_files_js_mentions_public_base_and_short_lived():
    js = (ROOT / "src/dashboard/static/js/files.js").read_text()
    assert "short-lived" in js
    assert "NEXTCLOUD_PUBLIC_URL" in js or "public tunnel" in js or "public Cloud base" in js
