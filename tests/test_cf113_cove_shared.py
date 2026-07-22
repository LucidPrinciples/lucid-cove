"""#CF-113 CoveShared — steward-owned operator-only RW share."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.dashboard.routes import nextcloud as ncr
from src.tools import nextcloud_tools as nc


def test_operator_gate_excludes_guests():
    assert ncr._is_operator_presence("member", "member") is True
    assert ncr._is_operator_presence("steward", "admin") is True
    assert ncr._is_operator_presence("member", "guest") is False


def test_cove_shared_constants():
    assert ncr.STEWARD_COVE_SHARED_FOLDER == "CoveShared"
    assert ncr._COVE_SHARED_RW_PERMS == 15  # RW no re-share


@pytest.mark.asyncio
async def test_share_cove_shared_posts_rw_user_share():
    """Steward OCS share uses path /CoveShared and permissions 15."""
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
    assert FakeClient.last["data"]["path"] == "/CoveShared"
    assert FakeClient.last["data"]["shareWith"] == "jag"
    assert FakeClient.last["data"]["permissions"] == 15
    assert FakeClient.last["data"]["shareType"] == 0


@pytest.mark.asyncio
async def test_share_skips_when_target_is_steward():
    ok = await ncr._share_cove_shared_with_operator(
        "admin", "x", "admin", "y")
    assert ok is True


def test_team_agents_denied_cove_shared(monkeypatch):
    """Non-steward team roles cannot use CoveShared as workspace."""
    # bind acting channel via ContextVar
    tok = nc.set_acting_channel("archimedes-day")
    try:
        # stub channel→role map if needed via resolve
        monkeypatch.setattr(
            nc, "resolve_acting_role", lambda: ("builder", "archimedes"))
        err = nc.check_nc_path_access("CoveShared/handoff.md", write=True)
        assert err and ("operators only" in err.lower() or "Access denied" in err)
        err = nc.check_nc_path_access("CoveShared", write=False)
        assert err and "Access denied" in err
    finally:
        nc.set_acting_channel(None) if False else None
        try:
            nc._acting_channel_ctx.reset(tok)
        except Exception:
            pass


def test_steward_may_use_cove_shared(monkeypatch):
    monkeypatch.setattr(nc, "resolve_acting_role", lambda: ("steward", "stuart"))
    assert nc.check_nc_path_access("CoveShared/notes.md", write=True) is None
