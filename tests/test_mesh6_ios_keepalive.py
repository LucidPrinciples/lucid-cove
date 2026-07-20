"""#MESH6 — iPhone keep-alive + host→phone instruction path.

iOS suspends Tailscale in the background; phone peer goes idle/offline and every
mesh-only MC fails together. We cannot fix iOS from Cove code — we document VPN
On Demand and the Connected-first split in every instruction surface operators
and phones actually see.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_mesh_join_html_has_ios_keepalive_step():
    html = (ROOT / "src/dashboard/static/mesh-join.html").read_text()
    assert "VPN On Demand" in html
    assert "Keep this phone on the mesh" in html
    assert "open Tailscale first" in html or "Open Tailscale first" in html
    assert "Connected" in html


def test_home_connect_mobile_mentions_vpn_on_demand():
    home = (ROOT / "src/dashboard/static/js/home.js").read_text()
    assert "VPN On Demand" in home
    assert "Connected" in home
    # Post-mint reminder when join code is shown
    assert home.count("VPN On Demand") >= 2


def test_settings_devices_mentions_vpn_on_demand():
    settings = (ROOT / "src/dashboard/static/js/settings-account.js").read_text()
    assert "VPN On Demand" in settings
    assert "Get join code" in settings


def test_mesh_md_end_to_end_and_troubleshoot():
    mesh = (ROOT / "MESH.md").read_text()
    assert "#MESH6" in mesh or "MESH6" in mesh
    assert "VPN On Demand" in mesh
    assert "End-to-end order" in mesh
    assert "idle; offline" in mesh or "last seen" in mesh
    assert "connect-mesh.sh" in mesh
    assert "tailscale status" in mesh
    # Hygiene + durability still present
    assert "#MESH-NAME" in mesh
    assert "accept-dns" in mesh
