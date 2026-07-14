"""Install-pass: provision mints LP_CADDY_ADMIN_TOKEN into the Cove stack and
shared-Caddy bootstrap so set-address /load works on a fresh install without
manual host patching.
"""
import os
from pathlib import Path

import provision.netconfig as nc
import provision.centralized as central


def test_build_env_emits_caddy_token_and_clone_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("LP_CADDY_ADMIN_TOKEN", "minted-token-xyz")
    cove = {"id": "testcove", "name": "Test", "domain": "", "timezone": "UTC"}
    op = {"handle": "alice", "name": "Alice", "email": ""}
    deploy = {
        "_host_dir": "/home/u/lucid-cove/out/testcove-cove",
        "lucid_cove_path": "/home/u/lucid-cove",
        "nextcloud_port": 8080,
        "matrix_port": 8008,
    }
    text = central.build_env(cove, op, ["openrouter"], {}, None, deploy)
    assert "LP_CADDY_ADMIN_TOKEN=minted-token-xyz" in text
    assert "COVE_HOST_DIR=/home/u/lucid-cove/out/testcove-cove" in text
    assert "COVE_CLONE_DIR=/home/u/lucid-cove" in text


def test_generate_cove_mints_token_and_writes_shared_env(monkeypatch, tmp_path):
    # Isolate from any ambient token
    monkeypatch.delenv("LP_CADDY_ADMIN_TOKEN", raising=False)
    # Avoid hub / external side effects
    monkeypatch.setenv("LP_REGISTRY_URL", "")
    monkeypatch.setattr(central, "_detect_mesh_ip", lambda: "")
    monkeypatch.setattr(central, "_detect_gpu_info", lambda: {"present": False})
    monkeypatch.setattr(central, "_host_timezone", lambda: "UTC")
    monkeypatch.setattr(central.netconfig, "preflight_ports",
                        lambda ports, target: ports)

    cfg = {
        "from_scratch": True,
        "cove": {"id": "freshcove", "name": "Fresh"},
        "operator": {},
        "deploy": {"target": "standalone", "lucid_cove_path": "/opt/lucid-cove"},
        "model_providers": ["openrouter"],
        "matrix": {"enabled": False},
        "team": "off",
    }
    out = tmp_path / "out"
    out.mkdir()
    result = central.generate_cove(cfg, out)

    stack_env = (out / "freshcove-cove" / ".env").read_text()
    assert "LP_CADDY_ADMIN_TOKEN=" in stack_env
    tok_line = [l for l in stack_env.splitlines() if l.startswith("LP_CADDY_ADMIN_TOKEN=")][0]
    tok = tok_line.split("=", 1)[1].strip()
    assert len(tok) >= 16  # minted, not empty

    shared_env = out / "_shared-caddy" / ".env"
    assert shared_env.is_file()
    assert f"LP_CADDY_ADMIN_TOKEN={tok}" in shared_env.read_text()

    base = (out / "_shared-caddy" / "Caddyfile").read_text()
    # Token was in provisioner env when base was rendered → #D35 mode
    assert "admin localhost:2018" in base
    assert "reverse_proxy localhost:2018" in base
    assert "admin :2019" not in base
    assert "s3cr3t" not in base and tok not in base  # secret never in Caddyfile


def test_caddy_load_sends_origin_and_host(monkeypatch):
    import provision.runtime_address as ra
    captured = {}

    class _Resp:
        def read(self):
            return b""
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def _ok(req, timeout=0):
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        return _Resp()

    monkeypatch.setattr(ra.urllib.request, "urlopen", _ok)
    monkeypatch.setenv("COVE_CADDY_ADMIN", "http://lucidcove-caddy:2019")
    monkeypatch.setenv("LP_CADDY_ADMIN_TOKEN", "s3cr3t")
    assert ra._caddy_load("{ }") == {"ok": True}
    h = captured["headers"]
    # Host comes from the URL via urllib (we no longer force it)
    assert h.get("origin") == "http://localhost:2018"  # #D35 loopback admin allowlist
    assert h.get("authorization") == "Bearer s3cr3t"
