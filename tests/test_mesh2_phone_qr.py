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

    # #MESH5: join URL is public hub, not mesh-only Cove apex
    monkeypatch.setattr(
        ob, "_public_mesh_join_origin", lambda request: "https://app.lucidcove.org"
    )
    monkeypatch.setattr(
        ob, "_cove_apex_origin", lambda: "https://clearfield.lucidcove.org"
    )
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
    assert out["join_url"].startswith("https://app.lucidcove.org/mesh-join?")
    parsed = urlparse(out["join_url"])
    qs = parse_qs(parsed.query)
    assert qs["k"] == ["tskey-auth-testkey123"]
    assert qs["s"] == ["https://headscale.lucidcove.org"]
    assert qs["c"] == ["https://clearfield.lucidcove.org"]
    assert out.get("cove_apex") == "https://clearfield.lucidcove.org"
    assert "qr_svg" in out and "<svg" in out["qr_svg"]
    assert "tailscale_authkey" in out["deep_links"]


def test_public_mesh_join_origin_defaults_to_hub(monkeypatch):
    """Stranger phone QR must not encode a mesh-only Cove apex (#MESH5)."""
    from src.dashboard.routes import onboarding as ob

    monkeypatch.delenv("LP_PUBLIC_BASE", raising=False)
    monkeypatch.delenv("LP_REGISTRY_URL", raising=False)
    monkeypatch.setattr(ob, "env_bool", lambda k: False)
    req = MagicMock()
    req.base_url = "http://127.0.0.1:8200/"
    assert ob._public_mesh_join_origin(req) == "https://app.lucidcove.org"


def test_public_mesh_join_origin_uses_registry_url(monkeypatch):
    from src.dashboard.routes import onboarding as ob

    monkeypatch.setenv("LP_REGISTRY_URL", "https://app.lucidcove.org/")
    monkeypatch.delenv("LP_PUBLIC_BASE", raising=False)
    req = MagicMock()
    assert ob._public_mesh_join_origin(req) == "https://app.lucidcove.org"


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
        "c": "https://ridgedale.lucidcove.org",
    }
    resp = await ob.mesh_join_page(req)
    body = resp.body.decode("utf-8")
    assert resp.status_code == 200
    assert "tskey-auth-abc" in body
    assert "headscale.lucidcove.org" in body
    assert "Connect this phone" in body
    # Install-first UX (#MESH4 follow-up): scan alone must not be the lead path
    assert "Install Tailscale first" in body
    assert "App Store" in body or "apps.apple.com" in body
    assert "does not install the app" in body
    # #MESH5: post-join Cove open only after Connected
    assert "ridgedale.lucidcove.org" in body
    assert "Open your Cove" in body
    assert "Connected" in body
