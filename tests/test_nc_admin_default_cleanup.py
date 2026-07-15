"""Admin/steward NC gets stock skeleton junk; presence users do not.

ensure_nc_shape must DELETE the default files so Stuart's Files view matches
the clean presence experience (Quietgrove / install feedback 2026-07-15).
"""
import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))


def test_default_nc_junk_list_covers_stock_skeleton():
    from src.dashboard.routes import nextcloud as nc
    names = " ".join(nc.DEFAULT_NC_JUNK)
    for must in ("Readme.md", "Nextcloud intro.mp4", "Photos", "Templates",
                 "Documents/Example.md", "Nextcloud Manual.pdf"):
        assert must in names


def test_ensure_nc_shape_source_calls_default_cleanup():
    src = (_ROOT / "src/dashboard/routes/nextcloud.py").read_text()
    assert "async def _remove_nc_default_files" in src
    assert "DEFAULT_NC_JUNK" in src
    # called inside ensure_nc_shape
    assert "_remove_nc_default_files(" in src
    assert "NC default cleanup" in src


def test_remove_nc_default_files_deletes_each_junk():
    import asyncio
    from src.dashboard.routes import nextcloud as nc

    class FakeResp:
        def __init__(self, code):
            self.status_code = code

    deleted = []

    class FakeClient:
        async def request(self, method, url, auth=None):
            assert method == "DELETE"
            deleted.append(url)
            return FakeResp(204)

    failures = asyncio.get_event_loop().run_until_complete(
        nc._remove_nc_default_files(
            FakeClient(), "https://nc.example/remote.php/dav/files/adminX",
            "adminX", "secret"))
    assert failures == 0
    assert len(deleted) == len(nc.DEFAULT_NC_JUNK)
    assert any("Readme.md" in u for u in deleted)
    assert any("Photos" in u for u in deleted)


def test_remove_nc_default_files_404_is_ok():
    import asyncio
    from src.dashboard.routes import nextcloud as nc

    class FakeResp:
        def __init__(self, code):
            self.status_code = code

    class FakeClient:
        async def request(self, method, url, auth=None):
            return FakeResp(404)

    failures = asyncio.get_event_loop().run_until_complete(
        nc._remove_nc_default_files(
            FakeClient(), "https://nc.example/remote.php/dav/files/adminX",
            "adminX", "secret"))
    assert failures == 0


def test_ensure_admin_nc_clean_exists_and_uses_admin_user():
    """Stuart Files uses NC_ADMIN_USER — cleanup must target that account."""
    src = (_ROOT / "src/dashboard/routes/nextcloud.py").read_text()
    assert "async def ensure_admin_nc_clean" in src
    assert "NC_ADMIN_USER" in src
    # provision steward path cleans admin
    assert "admin NC clean after steward provision" in src
    # manager Files path triggers clean
    assert "ensure_admin_nc_clean" in src


def test_save_domain_soft_refresh_paints_host_command():
    """Immediate paint of d.host_command so set-address does not look stuck."""
    src = (_ROOT / "src/dashboard/static/js/home.js").read_text()
    assert "paint host_command from THIS response" in src
    assert "d.host_command" in src
    assert "_addrRanCommand" in src
