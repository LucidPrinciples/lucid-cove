"""WebDAV PUT must MKCOL parents so Projects/{name}/plan.md does not 404."""

import pytest

from src.tools import nextcloud_tools as nc


@pytest.mark.asyncio
async def test_ensure_webdav_parents_mkcols(monkeypatch):
    calls = []

    class _Resp:
        def __init__(self, code=201):
            self.status_code = code

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, method, url, **k):
            calls.append((method, url))
            return _Resp(201)

    monkeypatch.setattr(nc, "_auth", lambda: ("u", "p"))
    monkeypatch.setattr(nc, "_webdav_url", lambda p: f"http://nc/{p}")
    monkeypatch.setattr(nc, "_norm_nc_path", lambda p: p.strip("/"))
    monkeypatch.setattr(nc.httpx, "AsyncClient", _Client)

    await nc._ensure_webdav_parents("Projects/Warehouse Liquidation/plan.md")
    assert ("MKCOL", "http://nc/Projects") in calls
    assert ("MKCOL", "http://nc/Projects/Warehouse Liquidation") in calls
    # must not MKCOL the file itself
    assert not any(u.endswith("plan.md") for _, u in calls)
