# CF-101 (batch7 #4) — Matrix regen-while-virgin. The destructive live regen (DB
# wipe + container recreate) needs docker/Dendrite/Postgres and is proven on a live
# box (run 3); these cover the SAFE, pure core: the virgin DECISION and the config
# regeneration (server_name stamped to matrix.{domain}).
import pathlib
import sys

# Import netconfig the SAME way test_provisioner_cli imports the CLI: put provision/
# on the path and import the module directly. Using `from provision import netconfig`
# would bind `provision` as a namespace package in sys.modules and shadow the CLI
# test's `import provision as cli` (provision/ has no __init__.py).
_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "provision"))
import netconfig  # noqa: E402


def test_localpart_of():
    assert netconfig._localpart_of("@stuart:matrix.localhost") == "stuart"
    assert netconfig._localpart_of("stuart") == "stuart"
    assert netconfig._localpart_of("") == ""


def test_virgin_when_only_agents():
    senders = ["@stuart:matrix.localhost", "@lt:matrix.localhost", "atlas"]
    assert netconfig.matrix_virgin_from_senders(senders, ["stuart", "lt", "atlas"]) is True


def test_virgin_empty_is_virgin():
    assert netconfig.matrix_virgin_from_senders([], ["stuart"]) is True


def test_not_virgin_when_a_human_present():
    senders = ["@stuart:matrix.localhost", "@jason:matrix.localhost"]
    assert netconfig.matrix_virgin_from_senders(senders, ["stuart", "lt"]) is False


def test_agent_match_is_case_insensitive():
    assert netconfig.matrix_virgin_from_senders(["@Stuart:x"], ["stuart"]) is True


def test_regenerate_dendrite_config_stamps_server_name():
    cfg = netconfig.regenerate_dendrite_config(
        domain="cove.example.org", db_password="pw", registration_shared_secret="s",
        bot_user_ids=["stuart", "lt"])
    assert "server_name: matrix.cove.example.org" in cfg
    assert '"@stuart:matrix.cove.example.org"' in cfg


def test_reconcile_no_domain_is_noop():
    r = netconfig.reconcile_matrix_identity(cove_id="c1", domain="")
    assert r["ok"] is False
    assert "no domain" in r["reason"]


def test_reconcile_virgin_check_unavailable_fails_safe(monkeypatch):
    # No docker in the sandbox → account read returns None → NOT changed, safe.
    monkeypatch.setattr(netconfig, "_dendrite_account_localparts",
                        lambda pg: (None, "docker not available"))
    r = netconfig.reconcile_matrix_identity(cove_id="c1", domain="cove.example.org")
    assert r["changed"] is False
    assert r["ok"] is False


def test_reconcile_not_virgin_reports_existing_conversations(monkeypatch):
    monkeypatch.setattr(netconfig, "_dendrite_account_localparts",
                        lambda pg: (["stuart", "jason"], "ok"))
    r = netconfig.reconcile_matrix_identity(
        cove_id="c1", domain="cove.example.org", agent_localparts=["stuart", "lt"])
    assert r["virgin"] is False
    assert r["changed"] is False
    assert "existing" in r["message"].lower()


def test_reconcile_virgin_gated_off_is_report_only(monkeypatch):
    monkeypatch.setattr(netconfig, "_dendrite_account_localparts",
                        lambda pg: (["stuart", "lt"], "ok"))
    monkeypatch.delenv("LP_MATRIX_REGEN_ENABLED", raising=False)
    r = netconfig.reconcile_matrix_identity(
        cove_id="c1", domain="cove.example.org", agent_localparts=["stuart", "lt"])
    assert r["virgin"] is True
    assert r["changed"] is False
    assert r["gated"] is True
    assert r["server_name"] == "matrix.cove.example.org"


# --- B9 (batch-9 #1): host-side regen wiring -------------------------------------

def test_reconcile_default_postgres_container_is_cove_postgres(monkeypatch):
    # FRESH single-stack Cove: Dendrite's DB lives in `{cove_id}-postgres`, NOT a
    # separate `{cove_id}-dendrite-postgres`. The virgin check must query the former.
    seen = {}

    def _cap(pg):
        seen["pg"] = pg
        return (None, "docker not available")

    monkeypatch.setattr(netconfig, "_dendrite_account_localparts", _cap)
    netconfig.reconcile_matrix_identity(cove_id="smith", domain="smith.example.org")
    assert seen["pg"] == "smith-postgres"


def test_rewrite_dendrite_server_name_host_side(tmp_path):
    d = tmp_path / "docker"
    d.mkdir()
    cfg = d / "dendrite.yaml"
    cfg.write_text("global:\n  server_name: matrix.smith.localhost\n  key_id: ed25519:auto\n")
    ok = netconfig._rewrite_dendrite_server_name(str(tmp_path), "smith", "matrix.smith.example.org")
    assert ok is True
    assert "server_name: matrix.smith.example.org" in cfg.read_text()
    # key line preserved
    assert "key_id: ed25519:auto" in cfg.read_text()


def test_rewrite_dendrite_server_name_missing_file_reports(tmp_path):
    r = netconfig._rewrite_dendrite_server_name(str(tmp_path), "smith", "matrix.x.org")
    assert r is not True
    assert "not found" in r


def test_apply_matrix_regen_order_stop_wipe_config_start(monkeypatch):
    # The run-3 fix: STOP dendrite -> WIPE db -> REWRITE config host-side -> START dendrite.
    order = []

    class _R:
        returncode = 0
        stderr = ""
        stdout = ""

    def _fake_run(cmd, **kw):
        # cmd like ["docker","stop",name] / ["docker","exec","-u","postgres",pg,"sh","-c",wipe]
        if cmd[:2] == ["docker", "stop"]:
            order.append("stop")
        elif cmd[:2] == ["docker", "start"]:
            order.append("start")
        elif cmd[1] == "exec":
            order.append("db_wipe")
        return _R()

    monkeypatch.setattr(netconfig.shutil, "which", lambda x: "/usr/bin/docker")
    monkeypatch.setattr(netconfig.subprocess, "run", _fake_run)
    monkeypatch.setattr(netconfig, "_rewrite_dendrite_server_name",
                        lambda cove_dir, cove_id, ns: order.append("config") or True)
    r = netconfig._apply_matrix_regen(
        cove_id="smith", domain="smith.example.org", new_server="matrix.smith.example.org",
        postgres_container="smith-postgres", dendrite_container="smith-dendrite",
        cove_dir="/inst")
    assert order == ["stop", "db_wipe", "config", "start"]
    assert r["changed"] is True
    assert r["server_name"] == "matrix.smith.example.org"


def test_apply_matrix_regen_no_docker_is_safe(monkeypatch):
    monkeypatch.setattr(netconfig.shutil, "which", lambda x: None)
    r = netconfig._apply_matrix_regen(
        cove_id="s", domain="s.org", new_server="matrix.s.org",
        postgres_container="s-postgres", dendrite_container="s-dendrite")
    assert r["changed"] is False
    assert "docker not available" in r["reason"]
