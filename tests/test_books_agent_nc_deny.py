"""Agent Nextcloud tools cannot read Presence Bookkeeping.

Humans and /books Process stay on-box. Chat tools (including steward and
presence-unbound) must not list, read, search-into, or fetch that tree.
"""
from unittest.mock import patch

import pytest

from src.tools import nextcloud_tools as nc


@pytest.fixture(autouse=True)
def _clear_acting_channel():
    tok = nc.set_acting_channel(None)
    yield
    nc.clear_acting_channel(tok)


@pytest.mark.parametrize("path,blocked", [
    ("Bookkeeping", True),
    ("/Bookkeeping", True),
    ("bookkeeping/Drop/stmt.pdf", True),
    ("Bookkeeping/Organize/BVTest.mapped.json", True),
    ("Bookkeeping/Returns/2025.pdf", True),
    ("AgentSkills/Inbox/note.md", False),
    ("Projects/notes.md", False),
    ("BookkeepingSecrets", False),
])
def test_is_bookkeeping_nc_path(path, blocked):
    assert nc._is_bookkeeping_nc_path(path) is blocked


def test_filter_bookkeeping_hits_drops_tree_keeps_others():
    hits = [
        "/Bookkeeping/Drop/a.pdf",
        "/AgentSkills/Inbox/a.md",
        "bookkeeping/Organize/x.mapped.json",
        "/Projects/plan.md",
    ]
    assert nc._filter_bookkeeping_hits(hits) == [
        "/AgentSkills/Inbox/a.md",
        "/Projects/plan.md",
    ]


@pytest.mark.parametrize("role,agent", [
    ("steward", "stuart"),
    ("builder", "archimedes"),
    ("merchant", "mercer"),
    (None, None),
])
def test_all_agents_denied_bookkeeping(monkeypatch, role, agent):
    monkeypatch.setattr(nc, "resolve_acting_role", lambda: (role, agent))
    for path in (
        "Bookkeeping",
        "Bookkeeping/Drop/JUNStatementImage.pdf",
        "Bookkeeping/Organize/BVTest.mapped.json",
        "Bookkeeping/Returns/return.pdf",
    ):
        err = nc.check_nc_path_access(path, write=False)
        assert err and "Access denied" in err, (role, path, err)
        assert "Bookkeeping" in err
        err = nc.check_nc_path_access(path, write=True)
        assert err and "Access denied" in err, (role, path, err)


def test_steward_still_reads_non_books(monkeypatch):
    monkeypatch.setattr(nc, "resolve_acting_role", lambda: ("steward", "stuart"))
    assert nc.check_nc_path_access("AgentSkills/Ops/note.md", write=False) is None
    assert nc.check_nc_path_access("AgentSkills/Ops/note.md", write=True) is None


@pytest.mark.asyncio
async def test_nextcloud_search_strips_bookkeeping_hits():
    class FakeResp:
        status_code = 207
        text = """<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:">
  <d:response><d:href>/remote.php/dav/files/admin/Bookkeeping/Drop/a.pdf</d:href></d:response>
  <d:response><d:href>/remote.php/dav/files/admin/AgentSkills/Inbox/ok.md</d:href></d:response>
</d:multistatus>
"""

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, *a, **k):
            return FakeResp()

    with patch.object(nc, "_nc_user", return_value="admin"), \
         patch.object(nc, "_nc_url", return_value="http://nc.test"), \
         patch.object(nc, "_auth", return_value=("admin", "x")), \
         patch.object(nc, "httpx") as hx:
        hx.AsyncClient = FakeClient
        out = await nc.nextcloud_search.coroutine("a")
    assert "Inbox/ok.md" in out
    assert "Bookkeeping" not in out
