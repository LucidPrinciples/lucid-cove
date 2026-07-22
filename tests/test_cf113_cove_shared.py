"""#CF-113 OperatorShared — human operators only; agents fully denied."""
from unittest.mock import MagicMock, patch

import pytest

from src.dashboard.routes import nextcloud as ncr
from src.dashboard.routes import files as files_mod
from src.tools import nextcloud_tools as nc


def test_operator_gate_excludes_guests():
    assert ncr._is_operator_presence("member", "member") is True
    assert ncr._is_operator_presence("steward", "admin") is True
    assert ncr._is_operator_presence("member", "guest") is False


def test_operator_shared_constants():
    assert ncr.STEWARD_COVE_SHARED_FOLDER == "OperatorShared"
    assert ncr._LEGACY_COVE_SHARED_FOLDER == "CoveShared"
    assert ncr._COVE_SHARED_RW_PERMS == 15
    assert files_mod.OPERATOR_SHARED_PREFIX == "OperatorShared"


@pytest.mark.asyncio
async def test_share_operator_shared_posts_rw_user_share():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = "<ocs><meta><statuscode>100</statuscode></meta></ocs>"

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, auth=None, headers=None, data=None):
            FakeClient.last = {"url": url, "auth": auth, "data": data}
            return mock_resp

        async def request(self, *a, **k):
            return MagicMock(status_code=201)

    with patch.object(ncr, "NC_URL", "http://nc.test"), \
         patch.object(ncr, "httpx") as hx:
        hx.AsyncClient = FakeClient
        ok = await ncr._share_cove_shared_with_operator(
            "jag", "secret", "admin", "adminpass")
    assert ok is True
    assert FakeClient.last["data"]["path"] == "/OperatorShared"
    assert FakeClient.last["data"]["shareWith"] == "jag"
    assert FakeClient.last["data"]["permissions"] == 15


@pytest.mark.asyncio
async def test_share_skips_when_target_is_steward_admin():
    ok = await ncr._share_cove_shared_with_operator(
        "admin", "x", "admin", "y")
    assert ok is True


@pytest.mark.parametrize("role,agent", [
    ("builder", "archimedes"),
    ("steward", "stuart"),
    ("merchant", "mercer"),
    ("auditor", "vera"),
])
def test_all_agents_denied_operator_shared(monkeypatch, role, agent):
    monkeypatch.setattr(nc, "resolve_acting_role", lambda: (role, agent))
    for path in ("OperatorShared", "OperatorShared/finances.xlsx",
                 "CoveShared/legacy.md"):
        err = nc.check_nc_path_access(path, write=True)
        assert err and "Access denied" in err, (role, path, err)
        err = nc.check_nc_path_access(path, write=False)
        assert err and "Access denied" in err, (role, path, err)


def test_path_helpers_files_mod():
    assert files_mod._is_operator_shared_path("OperatorShared") is True
    assert files_mod._is_operator_shared_path("OperatorShared/a.md") is True
    assert files_mod._is_operator_shared_path("CoveShared") is True
    assert files_mod._is_operator_shared_path("AgentSkills/Inbox") is False
    assert files_mod._item_is_operator_shared_name("OperatorShared") is True
    assert files_mod._item_is_operator_shared_name("AgentSkills") is False


@pytest.mark.asyncio
async def test_admin_files_guard_blocks_operator_shared(monkeypatch):
    monkeypatch.setattr(ncr, "NC_ADMIN_USER", "adminclearfield")
    msg = await files_mod._operator_shared_agent_guard(
        None, "OperatorShared/notes.md", "adminclearfield")
    assert msg and "operators" in msg.lower()
    # operator NC user is allowed through this guard
    msg = await files_mod._operator_shared_agent_guard(
        None, "OperatorShared/notes.md", "jag")
    assert msg is None
