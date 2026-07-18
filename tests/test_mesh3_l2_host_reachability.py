"""MESH3 L2 — host punchability Attention card + classify logic."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOME_JS = (ROOT / "src/dashboard/static/js/home.js").read_text()
ONBOARD = (ROOT / "src/dashboard/routes/onboarding.py").read_text()
SCRIPT = ROOT / "scripts/probe-host-reachability.sh"
UTIL = ROOT / "src/utils/host_reachability.py"


def test_probe_script_exists_and_is_executable_bits():
    assert SCRIPT.is_file()
    text = SCRIPT.read_text()
    assert "tailscale netcheck" in text or 'netcheck' in text
    assert "hard_to_reach" in text
    assert "41641" in text
    # Must not open public HTTP / tunnel language
    assert "cloudflare" not in text.lower()


def test_util_orthogonal_to_public_tunnel():
    text = UTIL.read_text()
    assert "reachability.py" in text  # documents the other feature
    assert "public internet" in text.lower() or "public tunnel" in text.lower()
    assert "host_reachability.json" in text


def test_onboarding_step_gated_on_foundation():
    assert '"id": "host_reachability"' in ONBOARD
    assert "_foundation and is_admin" in ONBOARD
    assert "onboarding_host_reachability_skip" in ONBOARD
    assert "/api/onboarding/host-reachability" in ONBOARD
    # Must not treat Cloudflare public tunnel as L2
    assert "enable_tunnel" not in ONBOARD.split("host_reachability")[1][:800]


def test_home_js_card_and_refresh():
    assert "host_reachability" in HOME_JS
    assert "refreshHostReachability" in HOME_JS
    assert "does not publish your Cove" in HOME_JS
    assert "UDP 41641" in HOME_JS


def test_classify_never_probed(tmp_path, monkeypatch):
    from src.utils import host_reachability as hr

    monkeypatch.setenv("COVE_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("COVE_HOST_DIR", raising=False)
    summary = hr.classify(hr.read_status())
    assert summary["probed"] is False
    assert summary["done"] is False
    assert summary["available_reason"] == "never_probed"
    assert "probe-host-reachability.sh" in summary["host_command"]


def test_classify_hard(tmp_path, monkeypatch):
    from src.utils import host_reachability as hr

    monkeypatch.setenv("COVE_CONFIG_DIR", str(tmp_path))
    payload = {
        "version": 1,
        "ts": "2099-01-01T00:00:00Z",  # far future = not stale
        "ok": True,
        "hard_to_reach": True,
        "reason": "no_port_mapping",
        "hint": "enable UPnP",
        "port_mapping": "",
    }
    (tmp_path / "host_reachability.json").write_text(json.dumps(payload))
    summary = hr.classify(hr.read_status())
    assert summary["probed"] is True
    assert summary["hard_to_reach"] is True
    assert summary["done"] is False
    assert summary["available_reason"] == "hard_to_reach"


def test_classify_easy_done(tmp_path, monkeypatch):
    from src.utils import host_reachability as hr

    monkeypatch.setenv("COVE_CONFIG_DIR", str(tmp_path))
    payload = {
        "version": 1,
        "ts": "2099-01-01T00:00:00Z",
        "ok": True,
        "hard_to_reach": False,
        "reason": "ok_port_mapped",
        "hint": "good",
        "port_mapping": "UPnP",
    }
    (tmp_path / "host_reachability.json").write_text(json.dumps(payload))
    summary = hr.classify(hr.read_status())
    assert summary["done"] is True
    assert summary["hard_to_reach"] is False


def test_host_command_uses_cove_host_dir(monkeypatch):
    from src.utils import host_reachability as hr

    monkeypatch.setenv("COVE_HOST_DIR", "/home/op/cove/out/my-cove")
    cmd = hr.host_probe_command()
    assert cmd.startswith("bash /home/op/cove/scripts/probe-host-reachability.sh")
    assert "--out /home/op/cove/out/my-cove/config/host_reachability.json" in cmd


def test_mesh_md_documents_l2():
    mesh = (ROOT / "MESH.md").read_text()
    assert "MESH3 L2" in mesh or "Host reachability" in mesh
    assert "41641" in mesh



def test_host_command_from_mountinfo_when_env_empty(monkeypatch):
    """Founder/stamped boxes without COVE_HOST_DIR still get absolute paths via mountinfo."""
    from src.utils import host_reachability as hr
    import src.dashboard.routes.runbooks as rb

    monkeypatch.delenv("COVE_HOST_DIR", raising=False)
    monkeypatch.delenv("COVE_COVE_DIR", raising=False)
    monkeypatch.delenv("COVE_CLONE_DIR", raising=False)
    monkeypatch.setattr(rb, "_host_paths", lambda: ("", ""))
    monkeypatch.setattr(
        hr,
        "_mountinfo_host_paths",
        lambda: (
            "/home/op/ClearfieldCove/out/clearfield-cove/config",
            "/home/op/ClearfieldCove/cove-core",
        ),
    )

    cmd = hr.host_probe_command()
    assert cmd.startswith(
        "bash /home/op/ClearfieldCove/cove-core/scripts/probe-host-reachability.sh"
    )
    assert (
        "--out /home/op/ClearfieldCove/out/clearfield-cove/config/host_reachability.json"
        in cmd
    )
    assert "./config/" not in cmd
