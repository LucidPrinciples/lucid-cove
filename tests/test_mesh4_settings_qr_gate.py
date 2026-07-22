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
    """Fresh device: mint from signed-in Cove; alternate server before Apple/Google."""
    settings = (ROOT / "src/dashboard/static/js/settings-account.js").read_text()
    home = (ROOT / "src/dashboard/static/js/home.js").read_text()
    join = (ROOT / "src/dashboard/static/mesh-join.html").read_text()
    assert "already signed into this Cove" in settings
    assert "Alternate Server" in settings or "alternate server" in settings
    assert "already signed into this Cove" in home or "Stay signed into this Cove" in home
    assert "Add Account Using Alternate Server" in home
    assert "already signed into your Cove" in join
    assert "Mac or iCloud password will not work" in join or "Mac password" in join
    assert "Add Account Using Alternate Server" in join
    assert "Scan alone does not install" in settings or "does not install or log in" in settings


def test_mesh_join_mac_path_not_apple_login():
    """Mac/iPhone stranger trap: IPN Apple login must be called out."""
    join = (ROOT / "src/dashboard/static/mesh-join.html").read_text()
    settings = (ROOT / "src/dashboard/static/js/settings-account.js").read_text()
    assert "Apple" in join and "join code" in join.lower()
    assert "IPN" in settings or "Apple ID" in settings
