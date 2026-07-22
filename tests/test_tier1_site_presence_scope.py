"""#TIER1 — two-tier Site Builder + presence privacy (Phase 1).

Acceptance:
- Site list is bound to acting NC creds only (no host-wide union).
- site.yaml create stamps tier + owner_presence_id.
- Steward no longer receives ambient shares of presence Sites/Content.
- Video body presence_name cannot retarget another signed-in presence.
- site_tools config resolve stays under get_sites_path().
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from src.dashboard.routes import nextcloud as nc
from src.dashboard.routes import sites as sites_mod
from src.dashboard.routes import video_pipeline as vp


def test_steward_shared_folders_exclude_sites_and_content():
    """Presence Sites + Content must not be shared to steward admin NC."""
    shared = list(nc.STEWARD_SHARED_FOLDERS)
    assert "AgentSkills/Sites" not in shared
    assert "AgentSkills/Content" not in shared
    assert "AgentSkills/Sites" in nc.PRESENCE_FOLDERS
    assert "AgentSkills/Content" in nc.PRESENCE_FOLDERS


def test_acting_site_tier_admin_vs_member():
    admin_req = SimpleNamespace(state=SimpleNamespace(presence={
        "id": "p-admin", "cove_role": "admin",
    }))
    member_req = SimpleNamespace(state=SimpleNamespace(presence={
        "id": "p-mem", "cove_role": "member",
    }))
    none_req = SimpleNamespace(state=SimpleNamespace(presence=None))
    assert sites_mod._acting_site_tier(admin_req) == "cove"
    assert sites_mod._acting_site_tier(member_req) == "presence"
    assert sites_mod._acting_site_tier(none_req) == "cove"
    assert sites_mod._acting_site_tier(None) == "cove"


def test_annotate_site_config_stamps_tier():
    cfg = {"domain": "a.example", "status": "setup"}
    out = sites_mod._annotate_site_config(
        cfg, "a.example", "presence", {"id": "pres-1"},
    )
    assert out["tier"] == "presence"
    assert out["owner_presence_id"] == "pres-1"
    # client cannot keep a forged cove tier on a presence door
    forged = {"domain": "a.example", "tier": "cove"}
    out2 = sites_mod._annotate_site_config(
        forged, "a.example", "presence", {"id": "pres-1"},
    )
    assert out2["tier"] == "presence"


@pytest.mark.asyncio
async def test_list_sites_uses_only_acting_nc_user(monkeypatch):
    """Two different NC users → two different site sets; no union."""
    calls = []

    async def fake_creds(request):
        p = getattr(request.state, "presence", {}) or {}
        user = p.get("nc_username") or "unknown"
        return ("http://nc", user, "pw")

    async def fake_propfind(client, url, nc_user, nc_pass):
        calls.append(nc_user)
        if nc_user == "alice":
            return ["alice-site.com"]
        if nc_user == "bob":
            return ["bob-site.com"]
        if nc_user == "adminclearfield":
            return ["cove-business.com"]
        return []

    async def fake_get(client, url, nc_user, nc_pass):
        # return minimal yaml based on folder in url
        for d in ("alice-site.com", "bob-site.com", "cove-business.com"):
            if d in url:
                return yaml.dump({"domain": d, "status": "setup"}).encode()
        return None

    monkeypatch.setattr(sites_mod, "get_nc_creds", fake_creds)
    monkeypatch.setattr(sites_mod, "_nc_propfind", fake_propfind)
    monkeypatch.setattr(sites_mod, "_nc_get", fake_get)
    monkeypatch.setattr(sites_mod, "get_sites_path", lambda: "AgentSkills/Sites")

    class _C:
        def __init__(self, *a, **k):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(sites_mod.httpx, "AsyncClient", _C)

    req_a = SimpleNamespace(state=SimpleNamespace(presence={
        "id": "a", "cove_role": "member", "nc_username": "alice",
    }))
    req_b = SimpleNamespace(state=SimpleNamespace(presence={
        "id": "b", "cove_role": "member", "nc_username": "bob",
    }))
    req_s = SimpleNamespace(state=SimpleNamespace(presence={
        "id": "s", "cove_role": "admin", "nc_username": "adminclearfield",
    }))

    sites_a = await sites_mod._list_sites_internal(req_a)
    sites_b = await sites_mod._list_sites_internal(req_b)
    sites_s = await sites_mod._list_sites_internal(req_s)

    domains_a = {s["domain"] for s in sites_a}
    domains_b = {s["domain"] for s in sites_b}
    domains_s = {s["domain"] for s in sites_s}

    assert domains_a == {"alice-site.com"}
    assert domains_b == {"bob-site.com"}
    assert "alice-site.com" not in domains_b
    assert "bob-site.com" not in domains_a
    assert "alice-site.com" not in domains_s
    assert "bob-site.com" not in domains_s
    assert domains_s == {"cove-business.com"}
    assert all(s.get("tier") == "presence" for s in sites_a)
    assert all(s.get("tier") == "cove" for s in sites_s)
    assert calls == ["alice", "bob", "adminclearfield"]


def test_reject_cross_presence_name():
    assert vp._reject_cross_presence_name("bob", "alice") is not None
    assert vp._reject_cross_presence_name("alice", "alice") is None
    assert vp._reject_cross_presence_name("Alice", "alice") is None
    assert vp._reject_cross_presence_name("bob", "") is None  # single-mode


@pytest.mark.asyncio
async def test_analyze_forbids_foreign_presence_name(monkeypatch):
    async def fake_session(request):
        return "alice"

    monkeypatch.setattr(vp, "_session_presence_name", fake_session)

    class Req:
        async def json(self):
            return {
                "stem": "x",
                "presence_name": "bob",  # attack
            }

    resp = await vp.analyze_transcript(Req())
    assert resp.status_code == 403
    assert "match" in resp.body.decode().lower() or b"match" in resp.body


def test_site_tools_config_scoped_to_sites_path(tmp_path, monkeypatch):
    from src.tools import site_tools as st
    import os

    def fake_get_sites_path():
        return "AgentSkills/Sites"

    monkeypatch.setattr("src.config.get_sites_path", fake_get_sites_path)

    vault = tmp_path / "vault"
    vault_sites = vault / "AgentSkills" / "Sites" / "mine.com"
    vault_sites.mkdir(parents=True)
    (vault_sites / "site.yaml").write_text("domain: mine.com\ngithub:\n  repo: o/r\n")

    original_exists = os.path.exists
    original_open = open

    def exists(path):
        s = str(path)
        if s.startswith("/vault/"):
            return original_exists(s.replace("/vault", str(vault), 1))
        if s.startswith("/app/data/"):
            return False
        return original_exists(path)

    def _open(path, *a, **k):
        s = str(path)
        if s.startswith("/vault/"):
            return original_open(s.replace("/vault", str(vault), 1), *a, **k)
        return original_open(path, *a, **k)

    monkeypatch.setattr(os.path, "exists", exists)
    monkeypatch.setattr("builtins.open", _open)

    cfg = st._get_site_config("mine.com")
    assert cfg["domain"] == "mine.com"

    with pytest.raises(ValueError):
        st._get_site_config("theirs.com")

    with pytest.raises(ValueError):
        st._get_site_config("../etc/passwd")
