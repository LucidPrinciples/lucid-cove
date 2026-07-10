# #D27 — System-tab runbooks rendered literal placeholders (<your lucid-cove clone>)
# even though the description promised paths "filled in for your box". Fill real per-box
# paths from env/cove.yaml; when unknown, emit an explicit actionable hint + flag, never
# silent generic text. Pure over _fill_paths / _host_paths.
import importlib

import src.dashboard.routes.runbooks as rb


def _clear(monkeypatch):
    for v in ("COVE_HOST_DIR", "COVE_CLONE_DIR", "COVE_COVE_DIR"):
        monkeypatch.delenv(v, raising=False)
    # neutralize cove.yaml deploy lookup so tests are hermetic
    monkeypatch.setattr(rb, "_host_paths", rb._host_paths)  # keep real, env-driven


def test_env_override_fills_clone_dir(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("COVE_CLONE_DIR", "/home/x/lucid-cove-core")
    monkeypatch.setattr("src.config.load_cove_config", lambda: {}, raising=False)
    d = rb._fill_paths({"steps": [{"command": "cd {{CLONE_DIR}} && git pull"}]})
    assert d["steps"][0]["command"] == "cd /home/x/lucid-cove-core && git pull"
    assert "path_hint" not in d["steps"][0]
    assert not d.get("paths_incomplete")


def test_host_dir_out_layout_derives_clone(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("COVE_HOST_DIR", "/home/x/clone/out/smith-cove")
    monkeypatch.setattr("src.config.load_cove_config", lambda: {}, raising=False)
    cove_dir, clone_dir = rb._host_paths()
    assert cove_dir == "/home/x/clone/out/smith-cove"
    assert clone_dir == "/home/x/clone"


def test_unknown_paths_emit_explicit_hint_and_flag(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setattr("src.config.load_cove_config", lambda: {}, raising=False)
    d = rb._fill_paths({"steps": [{"command": "cd {{CLONE_DIR}} && git pull", "label": "pull"}]})
    cmd = d["steps"][0]["command"]
    # not silent generic text — the placeholder names exactly what to set
    assert "COVE_CLONE_DIR" in cmd or "deploy.clone_dir" in cmd
    assert d["steps"][0]["path_hint"]
    assert "COVE_CLONE_DIR" in d["steps"][0]["path_hint"]
    assert d["paths_incomplete"] is True


def test_cove_yaml_deploy_block_fills(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setattr("src.config.load_cove_config",
                        lambda: {"deploy": {"clone_dir": "/srv/lc", "cove_dir": "/srv/lc/out/f-cove"}},
                        raising=False)
    cove_dir, clone_dir = rb._host_paths()
    assert clone_dir == "/srv/lc"
    assert cove_dir == "/srv/lc/out/f-cove"


def test_known_paths_leave_no_incomplete_flag(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("COVE_CLONE_DIR", "/c")
    monkeypatch.setenv("COVE_COVE_DIR", "/c/out/f-cove")
    monkeypatch.setattr("src.config.load_cove_config", lambda: {}, raising=False)
    d = rb._fill_paths({"steps": [{"command": "cd {{CLONE_DIR}} && cd {{COVE_DIR}}"}]})
    assert "{{" not in d["steps"][0]["command"]
    assert not d.get("paths_incomplete")
