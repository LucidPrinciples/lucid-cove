"""#MESH5 — stranger second-device bootstrap: public mesh-join + live DNS IP."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_connect_mesh_sh_passes_mesh_ip_to_reconcile():
    text = (ROOT / "provision" / "centralized.py").read_text()
    assert "api/domain/reconcile-dns" in text
    assert '"ip"' in text or "\\\"ip\\\"" in text or '{"ip"' in text
    # Fresh tailscale IP must be in the POST body (not bare reconcile)
    assert "MESH_IP" in text
    assert "-d " in text or "mesh_ip" in text.lower()


def test_mesh_join_html_has_cove_block_slot():
    html = (ROOT / "src/dashboard/static/mesh-join.html").read_text()
    assert "__COVE_BLOCK__" in html
    assert "Connected" in html


def test_reachable_prefers_live_over_stale_env(monkeypatch):
    from src.dashboard.routes import domain as d

    monkeypatch.setenv("COVE_MESH_IP", "100.64.0.14")  # stale other-cove IP
    monkeypatch.setattr(
        "provision.centralized._detect_mesh_ip",
        lambda: "100.64.0.15",
        raising=False,
    )
    # domain._reachable imports inside try — patch the provision path it uses
    import provision.centralized as C

    monkeypatch.setattr(C, "_detect_mesh_ip", lambda: "100.64.0.15")
    out = d._reachable({"deploy": {"mesh_ip": "100.64.0.99"}})
    assert out["ok"] is True
    assert out["ip"] == "100.64.0.15"
    assert out["source"] == "live"


def test_reachable_falls_back_to_env_when_no_live(monkeypatch):
    from src.dashboard.routes import domain as d
    import provision.centralized as C

    monkeypatch.setenv("COVE_MESH_IP", "100.64.0.15")
    monkeypatch.setattr(C, "_detect_mesh_ip", lambda: "")
    out = d._reachable({})
    assert out["ok"] is True
    assert out["ip"] == "100.64.0.15"
    assert out["source"] == "mesh"


@pytest.mark.asyncio
async def test_reconcile_dns_accepts_explicit_mesh_ip(monkeypatch):
    from src.dashboard.routes import domain as d

    monkeypatch.setattr(d, "load_cove_config", lambda: {"domain": "ridgedale.lucidcove.org"})

    captured = {}

    def fake_auto_dns(domain, deploy, cfg):
        captured["domain"] = domain
        captured["deploy"] = deploy
        return {"ok": True, "auto": True, "via": "hub", "ip": deploy.get("mesh_ip")}

    import types
    fake_C = types.SimpleNamespace(_auto_dns=fake_auto_dns)
    monkeypatch.setitem(__import__("sys").modules, "provision.centralized", fake_C)
    # also patch import path used inside reconcile
    monkeypatch.setattr(
        "provision.centralized._auto_dns", fake_auto_dns, raising=False
    )

    req = MagicMock()
    req.method = "POST"
    req.json = AsyncMock(return_value={"ip": "100.64.0.15"})
    req.query_params = {}

    # Ensure import provision as C works
    import provision
    import provision.centralized as real_C

    monkeypatch.setattr(real_C, "_auto_dns", fake_auto_dns)

    out = await d.reconcile_dns(req)
    assert out["ok"] is True
    assert out["ip"] == "100.64.0.15"
    assert out["source"] == "connect-mesh"
    assert captured["deploy"]["mesh_ip"] == "100.64.0.15"


@pytest.mark.asyncio
async def test_reconcile_dns_rejects_non_mesh_ip(monkeypatch):
    """Public / spoofed IPs must not be accepted on the public reconcile path."""
    from src.dashboard.routes import domain as d
    import provision.centralized as C

    monkeypatch.setattr(d, "load_cove_config", lambda: {"domain": "ridgedale.lucidcove.org"})
    monkeypatch.setattr(d, "_reachable", lambda cove: {"ok": True, "ip": "100.64.0.15", "source": "live"})

    captured = {}

    def fake_auto_dns(domain, deploy, cfg):
        captured["ip"] = deploy.get("mesh_ip")
        return {"ok": True, "auto": True, "via": "hub"}

    monkeypatch.setattr(C, "_auto_dns", fake_auto_dns)

    req = MagicMock()
    req.method = "POST"
    req.json = AsyncMock(return_value={"ip": "8.8.8.8"})  # not mesh CGNAT
    req.query_params = {}

    out = await d.reconcile_dns(req)
    assert out["ok"] is True
    # fell back to reachable, not 8.8.8.8
    assert out["ip"] == "100.64.0.15"
    assert captured["ip"] == "100.64.0.15"
