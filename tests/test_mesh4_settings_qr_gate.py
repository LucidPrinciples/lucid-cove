"""#MESH4 — Settings Devices mesh QR must not be gated on !domain."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "src/dashboard/static/js/settings-account.js").read_text()


def test_settings_devices_not_gated_on_missing_domain():
    """Regression: showMesh = hasAgent && !domain hid #MESH2 QR on Clearfield."""
    assert "hasAgent && !(MC.config && MC.config.domain)" not in JS
    assert "showMesh = hasAgent && !(MC.config && MC.config.domain)" not in JS
    # Join path is Presence-scoped, not LAN-only
    assert "showMeshJoin" in JS
    assert "Get join code" in JS
    assert "getDevicesMeshKey" in JS
    assert "devices-mesh-out" in JS


def test_settings_devices_keeps_approve_and_qr_together():
    """Approve paste-code and QR share the always-on mesh block."""
    assert "Approve this device" in JS
    assert "approveDeviceSettings" in JS
    # MESH4 copy: personal MC still needs mesh on domained Coves
    assert "personal Mission Control still needs it" in JS or "mesh-only even when" in JS
    assert "Phone — QR" in JS or "Get join code" in JS


def test_settings_and_home_say_install_tailscale_before_scan():
    """Fresh phone: copy must not lead with bare scan."""
    settings = (ROOT / "src/dashboard/static/js/settings-account.js").read_text()
    home = (ROOT / "src/dashboard/static/js/home.js").read_text()
    join = (ROOT / "src/dashboard/static/mesh-join.html").read_text()
    assert "Install Tailscale on the phone first" in settings
    assert "Install Tailscale on the phone first" in home
    assert "Install Tailscale first" in join
    assert "Scanning alone does not install" in settings or "Scan alone does not install" in settings
