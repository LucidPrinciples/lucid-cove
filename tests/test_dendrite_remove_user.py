"""
batch-10 #5 — full-table Dendrite user removal (netconfig.dendrite_remove_user).

The run-3 register-200-ghost: a PARTIAL delete (only userapi_accounts) leaves register
returning 200 for the localpart while login stays M_FORBIDDEN, so an in-app steward can
never self-heal. The fix clears the localpart from ALL userapi_* tables that key on it,
information_schema-driven. Live psql needs docker (proved next fresh run); here we test the
pure statement generation, the localpart guard, and the safe no-docker/invalid returns.
"""

import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "provision"))
import netconfig  # noqa: E402


def test_remove_user_statements_bind_not_interpolate():
    tables = ["userapi_accounts", "userapi_devices", "userapi_profiles"]
    stmts = netconfig._remove_user_statements(tables)
    assert stmts == [
        "DELETE FROM userapi_accounts WHERE localpart = :'lp'",
        "DELETE FROM userapi_devices WHERE localpart = :'lp'",
        "DELETE FROM userapi_profiles WHERE localpart = :'lp'",
    ]
    # The value is bound via the psql var, never concatenated into the statement.
    assert all("'lp'" in s and "localpart = :'lp'" in s for s in stmts)


def test_invalid_localpart_rejected_before_any_docker():
    for bad in ("", "Bad Upper", "semi;colon", "quote'x", "sp ace", "a" * 300):
        r = netconfig.dendrite_remove_user(localpart=bad, cove_id="smith")
        assert r["ok"] is False
        assert "invalid localpart" in r["reason"]


def test_valid_localparts_pass_the_guard(monkeypatch):
    # A valid localpart must get PAST the guard (fail later only for want of docker).
    monkeypatch.setattr(netconfig.shutil, "which", lambda _n: None)
    for good in ("steward", "stuart", "op-2afebcbb", "a.b_c=d/e-1"):
        r = netconfig.dendrite_remove_user(localpart=good, cove_id="smith")
        assert r["ok"] is False
        assert r["reason"] == "docker not available"  # passed the guard, stopped at docker


def test_needs_a_postgres_target(monkeypatch):
    monkeypatch.setattr(netconfig.shutil, "which", lambda _n: "/usr/bin/docker")
    r = netconfig.dendrite_remove_user(localpart="steward")  # no cove_id, no container
    assert r["ok"] is False
    assert "no postgres container" in r["reason"]


def test_derives_postgres_container_from_cove_id(monkeypatch):
    # With docker present + a cove_id, it should get to table discovery against
    # {cove_id}-postgres (we stub the discovery to observe the target + fail cleanly).
    monkeypatch.setattr(netconfig.shutil, "which", lambda _n: "/usr/bin/docker")
    seen = {}

    class _R:
        returncode = 1
        stdout = ""
        stderr = "boom"

    def _fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return _R()

    monkeypatch.setattr(netconfig.subprocess, "run", _fake_run)
    r = netconfig.dendrite_remove_user(localpart="steward", cove_id="smith")
    assert r["ok"] is False
    assert "smith-postgres" in seen["cmd"]
    assert "information_schema.columns" in " ".join(seen["cmd"])
