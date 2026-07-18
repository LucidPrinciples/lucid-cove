"""#MESH-NAME — clean hostnames at join; no junk localhost-/invalid- defaults."""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_clean_hostname_rejects_junk():
    from provision.mesh import clean_hostname

    assert clean_hostname("Jade iPhone") == "jade-iphone"
    assert clean_hostname("lp-homebase") == "lp-homebase"
    assert clean_hostname("localhost") == ""
    assert clean_hostname("localhost-s3aol1ce") == ""
    assert clean_hostname("invalid-0gbnz7oa") == ""
    assert clean_hostname("ip-192-168-1-15") == ""
    assert clean_hostname("") == ""
    assert clean_hostname("12345") == ""


def test_join_cmd_includes_hostname_when_clean(monkeypatch):
    from provision import mesh as m

    monkeypatch.setattr(m, "_suggested_hostname", lambda hint=None: "lp-homebase")
    cmd = m._join_cmd("https://headscale.lucidcove.org", "tskey-auth-x")
    assert "--accept-dns=true" in cmd
    assert "--hostname lp-homebase" in cmd
    assert "tskey-auth-x" in cmd


def test_join_cmd_omits_hostname_when_empty(monkeypatch):
    from provision import mesh as m

    cmd = m._join_cmd("https://headscale.lucidcove.org", "tskey-auth-x", hostname="")
    assert "--hostname" not in cmd
    assert "--accept-dns=true" in cmd


def test_connect_mesh_template_has_hostname_flag():
    from provision.centralized import CONNECT_MESH_SH

    assert "[hostname]" in CONNECT_MESH_SH
    assert "--hostname" in CONNECT_MESH_SH
    assert "COVE_ID" in CONNECT_MESH_SH
    assert "invalid-*" in CONNECT_MESH_SH or "invalid-" in CONNECT_MESH_SH


def test_mesh_join_page_mentions_naming():
    html = (ROOT / "src/dashboard/static/mesh-join.html").read_text()
    assert "MESH-NAME" in html or "Name this device" in html
    assert "tailscale set --hostname" in html
    assert "localhost" in html


def test_home_js_surfaces_hostname_help():
    js = (ROOT / "src/dashboard/static/js/home.js").read_text()
    assert "hostname_flag_help" in js
    assert "MESH-NAME" in js
    assert "tailscale set --hostname" in js


def test_mesh_md_documents_mesh_name():
    md = (ROOT / "MESH.md").read_text()
    assert "#MESH-NAME" in md or "MESH-NAME" in md
    assert "tailscale set --hostname" in md
