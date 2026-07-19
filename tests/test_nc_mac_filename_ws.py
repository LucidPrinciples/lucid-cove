"""macOS screenshot filenames embed U+202F (narrow no-break space) before AM/PM.

Models (and humans retyping list output) almost always pass a normal U+0020.
_read/_download already resolve via _find_sibling_by_ws; move/delete must too
so Jules archive and agent cleanup work without renaming files by hand.
"""
from __future__ import annotations

import pytest

from src.tools import nextcloud_tools as nc

NNBSP = "\u202f"  # narrow no-break space — macOS Screenshot … at 4.08.49 PM.png


def test_norm_ws_collapses_narrow_nbsp():
    mac = f"Screenshot 2026-07-18 at 4.08.49{NNBSP}PM.png"
    typed = "Screenshot 2026-07-18 at 4.08.49 PM.png"
    assert nc._norm_ws(mac) == nc._norm_ws(typed)
    assert " " in nc._norm_ws(mac)
    assert NNBSP not in nc._norm_ws(mac)


def test_norm_ws_collapses_nbsp_u00a0():
    assert nc._norm_ws("a\u00a0b") == "a b"


def test_norm_ws_preserves_non_space():
    assert nc._norm_ws("IMG_7977.HEIC") == "IMG_7977.HEIC"


@pytest.mark.asyncio
async def test_find_sibling_by_ws_matches_nnbsp(monkeypatch):
    """PROPFIND returns the real macOS name; lookup with plain space finds it."""
    real = f"Screenshot 2026-07-18 at 4.08.49{NNBSP}PM.png"
    typed_path = "AgentSkills/Inbox/Screenshot 2026-07-18 at 4.08.49 PM.png"

    class _Resp:
        status_code = 207
        text = f"""<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:">
  <d:response>
    <d:href>/remote.php/dav/files/admin/AgentSkills/Inbox/{real}</d:href>
    <d:propstat><d:prop><d:displayname>{real}</d:displayname></d:prop></d:propstat>
  </d:response>
</d:multistatus>"""

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, method, url, headers=None, content=None):
            assert method == "PROPFIND"
            return _Resp()

    monkeypatch.setattr(nc.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(nc, "_auth", lambda: ("u", "p"))
    monkeypatch.setattr(nc, "_webdav_url", lambda p: f"http://nc/dav/{p}")

    found = await nc._find_sibling_by_ws(typed_path)
    assert found == f"AgentSkills/Inbox/{real}"


@pytest.mark.asyncio
async def test_move_retries_with_sibling_on_404(monkeypatch):
    """nextcloud_move: first MOVE 404 → sibling resolve → second MOVE 201."""
    real = f"Screenshot 2026-07-18 at 4.08.49{NNBSP}PM.png"
    typed_src = "AgentSkills/Inbox/Screenshot 2026-07-18 at 4.08.49 PM.png"
    dest = "AgentSkills/Inbox/Archive/Screenshot 2026-07-18 at 4.08.49 PM.png"
    calls = []

    class _Resp:
        def __init__(self, code, text=""):
            self.status_code = code
            self.text = text

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, method, url, headers=None, content=None):
            calls.append((method, url, (headers or {}).get("Destination", "")))
            if method == "MKCOL":
                return _Resp(405)
            if method == "MOVE":
                # First attempt uses typed (plain space) src → 404
                if real not in url:
                    return _Resp(404)
                return _Resp(201)
            return _Resp(500, "unexpected")

    async def fake_sibling(path):
        assert "4.08.49 PM" in path or NNBSP in path
        return f"AgentSkills/Inbox/{real}"

    async def _ensure(path):
        return None

    monkeypatch.setattr(nc.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(nc, "_auth", lambda: ("u", "p"))
    monkeypatch.setattr(nc, "_webdav_url", lambda p: f"http://nc/dav/{p}")
    monkeypatch.setattr(nc, "check_nc_path_access", lambda path, write=False: None)
    monkeypatch.setattr(nc, "_ensure_own_team_workspace", _ensure)
    monkeypatch.setattr(nc, "_find_sibling_by_ws", fake_sibling)

    result = await nc.nextcloud_move.ainvoke(
        {"src": typed_src, "dest": dest, "overwrite": False}
    )
    assert result.startswith("Moved:"), result
    assert real in result
    move_urls = [u for m, u, _d in calls if m == "MOVE"]
    assert len(move_urls) == 2
    assert real in move_urls[1]


@pytest.mark.asyncio
async def test_delete_retries_with_sibling_on_404(monkeypatch):
    real = f"Screenshot 2026-07-18 at 4.08.49{NNBSP}PM.png"
    typed = "AgentSkills/Inbox/Screenshot 2026-07-18 at 4.08.49 PM.png"
    deletes = []

    class _Resp:
        def __init__(self, code):
            self.status_code = code
            self.text = ""

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def delete(self, url):
            deletes.append(url)
            if real not in url:
                return _Resp(404)
            return _Resp(204)

    async def fake_sibling(path):
        return f"AgentSkills/Inbox/{real}"

    monkeypatch.setattr(nc.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(nc, "_auth", lambda: ("u", "p"))
    monkeypatch.setattr(nc, "_webdav_url", lambda p: f"http://nc/dav/{p}")
    monkeypatch.setattr(nc, "check_nc_path_access", lambda path, write=False: None)
    monkeypatch.setattr(nc, "_find_sibling_by_ws", fake_sibling)

    result = await nc.nextcloud_delete.ainvoke({"path": typed})
    assert result.startswith("Deleted:"), result
    assert real in result
    assert len(deletes) == 2
