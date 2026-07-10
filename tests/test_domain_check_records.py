# batch-9 #7 (C1): the "Check my records" resolver must be safe (never raise) and honest
# about per-record match. We test the pure resolver + the record-verdict logic via a
# monkeypatched _resolve_a (no real DNS in the sandbox).
import sys
import pathlib

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
import src.dashboard.routes.domain as dom  # noqa: E402


@pytest.mark.asyncio
async def test_resolve_a_bogus_host_returns_empty():
    ip = await dom._resolve_a("nonexistent-lpcheck.invalid", timeout=1.0)
    assert ip == ""   # no raise, empty on failure


@pytest.mark.asyncio
async def test_check_records_all_ok_when_both_match(monkeypatch):
    monkeypatch.setattr(dom, "load_cove_config",
                        lambda: {"domain": "coolfam.org", "deploy": {"host_ip": "100.64.0.9"}})

    async def _ok_admin(request):
        return True

    monkeypatch.setattr(dom, "_is_admin_presence", _ok_admin)

    async def _fake_resolve(host, timeout=3.0):
        return "100.64.0.9"

    monkeypatch.setattr(dom, "_resolve_a", _fake_resolve)
    r = await dom.check_records(request=None)
    assert r["all_ok"] is True
    assert len(r["records"]) == 2
    assert all(rec["ok"] for rec in r["records"])


@pytest.mark.asyncio
async def test_check_records_flags_mismatch(monkeypatch):
    monkeypatch.setattr(dom, "load_cove_config",
                        lambda: {"domain": "coolfam.org", "deploy": {"host_ip": "100.64.0.9"}})

    async def _ok_admin(request):
        return True

    monkeypatch.setattr(dom, "_is_admin_presence", _ok_admin)

    async def _wrong(host, timeout=3.0):
        return "203.0.113.5"   # resolves, but not the expected box IP

    monkeypatch.setattr(dom, "_resolve_a", _wrong)
    r = await dom.check_records(request=None)
    assert r["all_ok"] is False
    assert r["records"][0]["resolved"] == "203.0.113.5"
    assert r["records"][0]["ok"] is False


# --- C2 (batch-9 #8): address change confirm carries the Matrix/Connect caveat ------------

@pytest.mark.asyncio
async def test_change_confirm_returns_matrix_note(monkeypatch):
    monkeypatch.setattr(dom, "load_cove_config",
                        lambda: {"domain": "old.lucidcove.org", "matrix": {"enabled": True}})

    async def _ok_admin(request):
        return True

    monkeypatch.setattr(dom, "_is_admin_presence", _ok_admin)
    body = dom.DomainSet(domain="new.lucidcove.org")   # confirm defaults False → guard fires
    resp = await dom.domain_set(body, request=None)
    # JSONResponse body
    import json as _j
    payload = _j.loads(bytes(resp.body).decode())
    assert payload["code"] == "confirm_change"
    assert payload["matrix_note"] and "Connect" in payload["matrix_note"]


@pytest.mark.asyncio
async def test_first_claim_has_no_change_note(monkeypatch):
    # No current domain → first claim → no confirm gate, no matrix change-note path.
    monkeypatch.setattr(dom, "load_cove_config", lambda: {"domain": "", "matrix": {"enabled": True}})

    async def _ok_admin(request):
        return True

    monkeypatch.setattr(dom, "_is_admin_presence", _ok_admin)
    # runtime_address unavailable + no docker → host-caddy path; persist would run, so stub it.
    monkeypatch.setattr(dom, "save_cove_config", lambda *_a, **_k: True)
    # #D31 — HERMETIC: this exercises domain_set, which falls through to the host-Caddy
    # reconcile (ensure_dns + install_caddy_snippet → live caddy reload, NC occ, Matrix
    # regen). That is EXACTLY the layer whose live side-effect took a production Cove down
    # (incident 2026-07-10). Fake the whole netconfig layer so the test verifies the route's
    # response shape without ever rendering/reloading a real caddy snippet or calling occ.
    from provision import netconfig as _nc
    monkeypatch.setattr(_nc, "ensure_dns", lambda *a, **k: {"ok": False, "reason": "mocked"})
    monkeypatch.setattr(_nc, "install_caddy_snippet",
                        lambda *a, **k: {"installed": False, "reloaded": False, "reason": "mocked"})
    monkeypatch.setattr(_nc, "reconcile_nextcloud_https", lambda *a, **k: {"ok": False, "reason": "mocked"})
    monkeypatch.setattr(_nc, "reconcile_matrix_identity", lambda *a, **k: {"ok": False, "reason": "mocked"})
    body = dom.DomainSet(domain="fresh.lucidcove.org")
    resp = await dom.domain_set(body, request=None)
    # dict return (host-caddy path) — no confirm_change, no change note injected
    assert isinstance(resp, dict)
    assert not any("Changing the address" in s for s in resp.get("next_steps", []))
