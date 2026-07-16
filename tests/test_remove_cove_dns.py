"""remove_cove_dns — deprovision mirror of ensure_cove_dns.

Throwaway/test Coves must not strand Cloudflare A/CNAME records. These tests
mock the CF API (no network, no token) and cover:
  - deletes apex + wildcard + _acme-challenge
  - idempotent when records are already gone
  - refuses zone-apex / bare-domain targets
  - registry DELETE hook calls remove for lucidcove.org domains
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from provision import cloudflare_dns as cfd


class _FakeResp:
    def __init__(self, payload=None, status=200):
        self._payload = payload if payload is not None else {"result": []}
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class _FakeClient:
    """Minimal stand-in for httpx.Client used by remove_cove_dns."""

    def __init__(self, records_by_name=None):
        # records_by_name: {name: [{id, type}, ...]}
        self.records = {k.rstrip("."): list(v) for k, v in (records_by_name or {}).items()}
        self.deleted = []
        self.gets = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, params=None):
        params = params or {}
        if url.endswith("/zones"):
            return _FakeResp({"result": [{"id": "zone-1"}]})
        name = (params.get("name") or "").rstrip(".")
        rtype = params.get("type")
        self.gets.append((name, rtype))
        recs = [r for r in self.records.get(name, []) if r.get("type") == rtype]
        return _FakeResp({"result": recs})

    def delete(self, url):
        rid = url.rstrip("/").split("/")[-1]
        self.deleted.append(rid)
        # drop from in-memory store
        for name, recs in list(self.records.items()):
            self.records[name] = [r for r in recs if r.get("id") != rid]
        return _FakeResp({"result": {"id": rid}})

    def post(self, *a, **k):
        raise AssertionError("remove_cove_dns must not create records")

    def put(self, *a, **k):
        raise AssertionError("remove_cove_dns must not update records")


def _patch_client(fake):
    return patch.object(cfd, "_client", return_value=fake)


def _patch_token():
    return patch.object(cfd, "_token", return_value="test-token")


def test_remove_cove_dns_deletes_apex_wildcard_and_acme():
    domain = "throwaway.lucidcove.org"
    fake = _FakeClient({
        domain: [
            {"id": "a1", "type": "A", "content": "100.64.0.9"},
        ],
        f"*.{domain}": [
            {"id": "a2", "type": "A", "content": "100.64.0.9"},
        ],
        f"_acme-challenge.{domain}": [
            {"id": "c1", "type": "CNAME", "content": "x.acme-dns"},
        ],
    })
    with _patch_token(), _patch_client(fake):
        res = cfd.remove_cove_dns(domain)
    assert res["ok"] is True
    assert res["domain"] == domain
    assert set(fake.deleted) == {"a1", "a2", "c1"}
    joined = " | ".join(res["actions"])
    assert "deleted A throwaway.lucidcove.org" in joined
    assert "deleted A *.throwaway.lucidcove.org" in joined
    assert "deleted CNAME _acme-challenge.throwaway.lucidcove.org" in joined


def test_remove_cove_dns_idempotent_when_absent():
    domain = "gone.lucidcove.org"
    fake = _FakeClient({})
    with _patch_token(), _patch_client(fake):
        res = cfd.remove_cove_dns(domain)
    assert res["ok"] is True
    assert fake.deleted == []
    assert any("absent" in a for a in res["actions"])


def test_remove_cove_dns_also_clears_tunnel_cname_at_apex():
    """If a Cove was switched to tunnel (proxied CNAME), remove still clears it."""
    domain = "tunneled.lucidcove.org"
    fake = _FakeClient({
        domain: [
            {"id": "cn1", "type": "CNAME", "content": "abc.cfargotunnel.com"},
        ],
        f"*.{domain}": [
            {"id": "a9", "type": "A", "content": "100.64.0.1"},
        ],
    })
    with _patch_token(), _patch_client(fake):
        res = cfd.remove_cove_dns(domain)
    assert res["ok"] is True
    assert set(fake.deleted) == {"cn1", "a9"}


@pytest.mark.parametrize("bad", [
    "lucidcove.org",
    "example.com",
    "",
    "   ",
    "localhost",
])
def test_remove_cove_dns_refuses_zone_apex(bad):
    with pytest.raises(ValueError):
        cfd.remove_cove_dns(bad)


def test_assert_safe_cove_domain_accepts_subdomain():
    assert cfd._assert_safe_cove_domain("cracker.lucidcove.org") == "cracker.lucidcove.org"
    assert cfd._assert_safe_cove_domain("  Foo.LucidCove.ORG. ") == "foo.lucidcove.org"


# ── registry DELETE hook (unit, no real DB) ───────────────────────────────────

@pytest.mark.asyncio
async def test_delete_cove_endpoint_calls_remove_dns(monkeypatch):
    """DELETE /api/registry/cove/{key} must invoke remove_cove_dns for lucidcove.org domains."""
    from src.dashboard.routes import registry as reg

    row = {
        "cove_id": "c-throw",
        "name": "Throwaway",
        "owner_handle": "tester",
        "domain": "throwaway.lucidcove.org",
        "homeserver": "",
        "space_id": None,
        "mesh_ip": "100.64.0.9",
    }
    executed = []

    class _Cur:
        def __init__(self, fetch):
            self._fetch = fetch

        async def fetchone(self):
            return self._fetch

    class _Conn:
        async def execute(self, sql, params=None):
            executed.append((sql.strip().split()[0].upper(), sql, params))
            if "SELECT" in sql.upper():
                return _Cur(row)
            return _Cur(None)

    class _DBCM:
        async def __aenter__(self):
            return _Conn()

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr("src.memory.database.get_db", lambda: _DBCM())

    async def _auth(request, conn, owner_handle=""):
        return {"mode": "fleet"}

    monkeypatch.setattr(reg, "_authorize_write", _auth)

    called = {}

    def _remove(domain):
        called["domain"] = domain
        return {"ok": True, "domain": domain, "actions": ["deleted A " + domain]}

    monkeypatch.setattr(
        "provision.cloudflare_dns.remove_cove_dns", _remove, raising=False)
    # import path used inside the endpoint
    import provision.cloudflare_dns as _mod
    monkeypatch.setattr(_mod, "remove_cove_dns", _remove)

    req = MagicMock()
    res = await reg.delete_cove("c-throw", req)
    assert res["ok"] is True
    assert res["cove_id"] == "c-throw"
    assert called.get("domain") == "throwaway.lucidcove.org"
    assert res["dns"]["ok"] is True
    # UPDATE handles + DELETE cove
    verbs = [e[0] for e in executed]
    assert "UPDATE" in verbs
    assert "DELETE" in verbs


@pytest.mark.asyncio
async def test_delete_cove_skips_dns_for_non_lucidcove_domain(monkeypatch):
    from src.dashboard.routes import registry as reg

    row = {
        "cove_id": "c-custom",
        "name": "Custom",
        "owner_handle": "tester",
        "domain": "cove.example.com",
    }

    class _Cur:
        async def fetchone(self):
            return row

    class _Conn:
        async def execute(self, sql, params=None):
            if "SELECT" in sql.upper():
                return _Cur()
            return SimpleNamespace(fetchone=lambda: None)

    class _DBCM:
        async def __aenter__(self):
            return _Conn()

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr("src.memory.database.get_db", lambda: _DBCM())
    monkeypatch.setattr(reg, "_authorize_write",
                        lambda *a, **k: _async_fleet())

    async def _async_fleet(*a, **k):
        return {"mode": "fleet"}

    monkeypatch.setattr(reg, "_authorize_write", _async_fleet)

    def _remove(_domain):
        raise AssertionError("must not call remove_cove_dns for non-lucidcove domain")

    import provision.cloudflare_dns as _mod
    monkeypatch.setattr(_mod, "remove_cove_dns", _remove)

    res = await reg.delete_cove("c-custom", MagicMock())
    assert res["ok"] is True
    assert res["dns"].get("skipped") is True
