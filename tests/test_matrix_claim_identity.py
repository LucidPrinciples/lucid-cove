"""Claim-time Matrix identity for Form Haven (Calhoun 2026-07-15).

Root cause: set_domain host command reported matrix_identity virgin=false / changed=false
while Dendrite stayed on matrix.{cove-id}.localhost. Haven UI invited
@user:matrix.{domain} → Dendrite Unrecognised server name.

Fixes covered here:
  - standard team is always in the agent allowlist (even with --agents empty)
  - first-claim allows the operator localpart so Open chat before mark-live does not lock
  - already-correct server_name short-circuits without wipe
  - host command embeds --agents / --operators
  - Form Haven preflight refuses stale .localhost identity with product copy
"""
import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "provision"))
sys.path.insert(0, str(_ROOT))
import netconfig  # noqa: E402


TEAM = [
    "stuart", "mercer", "archimedes", "arthur", "gabe", "ezra",
    "julian", "iris", "vera", "soren",
]


def test_standard_team_alone_is_virgin_without_agents_arg():
    assert netconfig.matrix_virgin_from_senders(TEAM, None) is True
    assert netconfig.matrix_virgin_from_senders(TEAM, []) is True


def test_operator_alone_is_not_classic_virgin():
    assert netconfig.matrix_virgin_from_senders(TEAM + ["mark"], None) is False


def test_first_claim_allows_operator_with_team():
    assert netconfig.matrix_first_claim_eligible(TEAM + ["mark"], None, ["mark"]) is True


def test_first_claim_blocks_extra_human():
    assert netconfig.matrix_first_claim_eligible(
        TEAM + ["mark", "jeff"], None, ["mark"]) is False


def test_expand_includes_havensteward():
    expanded = netconfig.expand_matrix_agent_localparts(["custombot"])
    assert "custombot" in expanded
    assert "havensteward" in expanded
    assert "stuart" in expanded


def test_reconcile_first_claim_with_operator_is_eligible_gated(monkeypatch, tmp_path):
    d = tmp_path / "docker"
    d.mkdir()
    (d / "dendrite.yaml").write_text(
        "global:\n  server_name: matrix.lucidcove-abc.localhost\n")
    monkeypatch.setattr(
        netconfig, "_dendrite_account_localparts",
        lambda pg: (TEAM + ["mark"], "ok"))
    monkeypatch.delenv("LP_MATRIX_REGEN_ENABLED", raising=False)
    r = netconfig.reconcile_matrix_identity(
        cove_id="abc", domain="calhoun.lucidcove.org",
        agent_localparts=None, operator_localparts=["mark"],
        first_claim=True, cove_dir=str(tmp_path))
    assert r["changed"] is False
    assert r.get("gated") is True
    assert r["server_name"] == "matrix.calhoun.lucidcove.org"
    assert "can be regenerated" in r["message"]


def test_reconcile_enabled_first_claim_calls_apply(monkeypatch, tmp_path):
    d = tmp_path / "docker"
    d.mkdir()
    (d / "dendrite.yaml").write_text(
        "global:\n  server_name: matrix.lucidcove-abc.localhost\n")
    monkeypatch.setattr(
        netconfig, "_dendrite_account_localparts",
        lambda pg: (TEAM + ["mark"], "ok"))
    monkeypatch.setenv("LP_MATRIX_REGEN_ENABLED", "1")
    called = {}

    def _apply(**kw):
        called.update(kw)
        return {"ok": True, "changed": True, "server_name": kw["new_server"], "steps": {}}

    monkeypatch.setattr(netconfig, "_apply_matrix_regen", _apply)
    r = netconfig.reconcile_matrix_identity(
        cove_id="abc", domain="calhoun.lucidcove.org",
        operator_localparts=["mark"], first_claim=True, cove_dir=str(tmp_path))
    assert called.get("new_server") == "matrix.calhoun.lucidcove.org"
    assert r["changed"] is True
    assert "Regenerated Matrix identity" in r["message"]


def test_reconcile_already_correct_skips_wipe(monkeypatch, tmp_path):
    d = tmp_path / "docker"
    d.mkdir()
    (d / "dendrite.yaml").write_text(
        "global:\n  server_name: matrix.calhoun.lucidcove.org\n")
    # If we wrongly wiped, this would be consulted — ensure we never need it.
    monkeypatch.setattr(
        netconfig, "_dendrite_account_localparts",
        lambda pg: (_ for _ in ()).throw(AssertionError("should not query accounts")))
    r = netconfig.reconcile_matrix_identity(
        cove_id="abc", domain="calhoun.lucidcove.org", cove_dir=str(tmp_path),
        enabled=True)
    assert r["already_correct"] is True
    assert r["changed"] is False


def test_reconcile_extra_human_stays_locked(monkeypatch, tmp_path):
    d = tmp_path / "docker"
    d.mkdir()
    (d / "dendrite.yaml").write_text(
        "global:\n  server_name: matrix.lucidcove-abc.localhost\n")
    monkeypatch.setattr(
        netconfig, "_dendrite_account_localparts",
        lambda pg: (TEAM + ["mark", "jeff"], "ok"))
    monkeypatch.setenv("LP_MATRIX_REGEN_ENABLED", "1")
    r = netconfig.reconcile_matrix_identity(
        cove_id="abc", domain="calhoun.lucidcove.org",
        operator_localparts=["mark"], first_claim=True, cove_dir=str(tmp_path))
    assert r["changed"] is False
    assert r["virgin"] is False
    assert "Form Haven" in r["message"]


def test_host_command_embeds_agents_and_operators_flags():
    domain_py = (_ROOT / "src/dashboard/routes/domain.py").read_text()
    assert "--agents" in domain_py
    assert "--operators" in domain_py
    assert "_operator_csv" in domain_py
    assert "LP_MATRIX_REGEN_ENABLED=1" in domain_py
    # Quietgrove: instance .env restamp needs --cove-dir on the host command
    assert "--cove-dir" in domain_py
    assert "_cove_dir_flag" in domain_py
    assert "_host_instance_dir" in domain_py


def test_haven_preflight_blocks_localhost_identity(monkeypatch):
    import src.dashboard.routes.matrix_haven as mh
    monkeypatch.setattr(mh, "_server_name", lambda: "matrix.calhoun.lucidcove.org")
    monkeypatch.setattr(
        mh, "_live_dendrite_server_name",
        lambda: "matrix.lucidcove-c0fb4da2c7e3c19b.localhost")
    r = mh._matrix_identity_ready_for_haven()
    assert r["ok"] is False
    assert "install address" in r["message"].lower() or "localhost" in r["message"]
    assert "Form Haven" in r["message"]


def test_haven_preflight_ok_when_matched(monkeypatch):
    import src.dashboard.routes.matrix_haven as mh
    monkeypatch.setattr(mh, "_server_name", lambda: "matrix.calhoun.lucidcove.org")
    monkeypatch.setattr(mh, "_live_dendrite_server_name",
                        lambda: "matrix.calhoun.lucidcove.org")
    r = mh._matrix_identity_ready_for_haven()
    assert r["ok"] is True


def test_haven_js_checks_matrix_ready():
    js = (_ROOT / "src/dashboard/static/js/haven.js").read_text()
    assert "/api/haven/matrix-ready" in js
    assert "haven-matrix-ready" in js
    assert "_havenCheckMatrixReady" in js


def test_set_domain_accepts_operators_arg():
    set_domain = (_ROOT / "provision/set_domain.py").read_text()
    assert "--operators" in set_domain
    assert "operator_localparts" in set_domain
    assert "first_claim=True" in set_domain
