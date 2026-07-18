"""#MESH2 — phone join QR + /mesh-join helper page."""
from __future__ import annotations

from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlparse

import pytest


def test_qr_svg_emits_svg():
    from src.dashboard.qr_svg import qr_svg

    svg = qr_svg("https://example.lucidcove.org/mesh-join?s=https://headscale.lucidcove.org&k=tskey-auth-x")
    assert "<svg" in svg
    assert "path" in svg or "rect" in svg


def test_enrich_mesh_key_payload_adds_join_url_and_qr(monkeypatch):
    from src.dashboard.routes import onboarding as ob

    monkeypatch.setattr(ob, "_cove_public_origin", lambda request: "https://clearfield.lucidcove.org")
    monkeypatch.setattr(ob, "_mesh_login_server", lambda res=None: "https://headscale.lucidcove.org")

    req = MagicMock()
    res = {
        "ok": True,
        "key": "tskey-auth-testkey123",
        "login_server": "https://headscale.lucidcove.org",
        "join_cmd": (
            "tailscale up --login-server https://headscale.lucidcove.org "
            "--authkey tskey-auth-testkey123 --accept-dns=true"
        ),
    }
    out = ob._enrich_mesh_key_payload(res, req)
    assert out["ok"] is True
    assert out["join_url"].startswith("https://clearfield.lucidcove.org/mesh-join?")
    parsed = urlparse(out["join_url"])
    qs = parse_qs(parsed.query)
    assert qs["k"] == ["tskey-auth-testkey123"]
    assert qs["s"] == ["https://headscale.lucidcove.org"]
    assert "qr_svg" in out and "<svg" in out["qr_svg"]
    assert "tailscale_authkey" in out["deep_links"]


def test_enrich_skips_failed_payload():
    from src.dashboard.routes import onboarding as ob

    req = MagicMock()
    out = ob._enrich_mesh_key_payload({"ok": False, "reason": "nope"}, req)
    assert out["ok"] is False
    assert "join_url" not in out


@pytest.mark.asyncio
async def test_mesh_join_page_renders_key():
    from src.dashboard.routes import onboarding as ob

    req = MagicMock()
    req.query_params = {
        "s": "https://headscale.lucidcove.org",
        "k": "tskey-auth-abc",
    }
    resp = await ob.mesh_join_page(req)
    body = resp.body.decode("utf-8")
    assert resp.status_code == 200
    assert "tskey-auth-abc" in body
    assert "headscale.lucidcove.org" in body
    assert "Connect this phone" in body
