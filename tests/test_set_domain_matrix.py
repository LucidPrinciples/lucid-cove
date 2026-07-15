# B9 (batch-9 #1): set_domain.py host-side Matrix wiring — the .env restamp after a
# regen, and that the matrix reconcile is threaded with the fresh-stack container names.
import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "provision"))
import set_domain  # noqa: E402


def test_restamp_matrix_env_rewrites_existing_and_appends(tmp_path):
    env = tmp_path / ".env"
    env.write_text("PORT=8204\nMATRIX_SERVER_NAME=matrix.smith.localhost\nFOO=bar\n")
    r = set_domain._restamp_matrix_env(str(tmp_path), "smith.example.org")
    assert r["ok"] is True
    body = env.read_text()
    assert "MATRIX_SERVER_NAME=matrix.smith.example.org" in body
    assert "MATRIX_PUBLIC_URL=https://matrix.smith.example.org" in body   # appended
    assert "PORT=8204" in body and "FOO=bar" in body                       # untouched
    assert "matrix.smith.localhost" not in body                            # old value gone
    assert "docker compose up -d app" in r["recreate"]


def test_restamp_matrix_env_no_dir_gives_manual_instructions():
    r = set_domain._restamp_matrix_env("", "smith.example.org")
    assert r["ok"] is False
    assert "MATRIX_SERVER_NAME=matrix.smith.example.org" in r["reason"]


def test_reconcile_matrix_identity_uses_fresh_stack_container(monkeypatch, tmp_path):
    # set_domain threads the default fresh-stack names into netconfig; with no docker the
    # reconcile fails safe (virgin unknown) and never restamps.
    import netconfig
    seen = {}
    monkeypatch.setattr(netconfig, "_dendrite_account_localparts",
                        lambda pg: seen.__setitem__("pg", pg) or (None, "docker not available"))

    class _Args:
        cove_id = "smith"
        agents = "stuart,atlas"
        cove_dir = str(tmp_path)
        compose_dir = str(tmp_path)
        postgres_container = ""
        dendrite_container = ""

    result = {}
    set_domain._reconcile_matrix_identity(_Args(), "smith.example.org", result)
    assert seen["pg"] == "smith-postgres"
    assert result["matrix_identity"]["changed"] is False
    assert "matrix_env" not in result   # no regen => no restamp


def test_reconcile_restamps_when_already_correct(monkeypatch, tmp_path):
    """Quietgrove: Dendrite already matrix.{domain} but app env still localhost —
    restamp + recreate must still run (not only when mx['changed'])."""
    import netconfig
    env = tmp_path / ".env"
    env.write_text("MATRIX_SERVER_NAME=matrix.lucidcove-aa9f.localhost\n")

    monkeypatch.setattr(
        netconfig, "reconcile_matrix_identity",
        lambda **kw: {
            "ok": True, "virgin": True, "changed": False, "already_correct": True,
            "server_name": "matrix.quietgrove.lucidcove.org",
            "current_server_name": "matrix.quietgrove.lucidcove.org",
            "message": "Matrix identity already matrix.quietgrove.lucidcove.org.",
        })
    seen = {}
    def _fake_run(cmd, cwd=None, capture_output=None, text=None, timeout=None):
        seen["cmd"] = cmd
        seen["cwd"] = cwd
        class R:
            returncode = 0
            stderr = ""
        return R()
    import subprocess as _sp
    monkeypatch.setattr(_sp, "run", _fake_run)

    class _Args:
        cove_id = "lucidcove-aa9f5ad9aa5a96da"
        agents = "stuart"
        operators = "jag"
        cove_dir = str(tmp_path)
        compose_dir = str(tmp_path)
        postgres_container = ""
        dendrite_container = ""

    result = {}
    set_domain._reconcile_matrix_identity(_Args(), "quietgrove.lucidcove.org", result)
    assert "matrix_env" in result and result["matrix_env"]["ok"] is True
    body = env.read_text()
    assert "MATRIX_SERVER_NAME=matrix.quietgrove.lucidcove.org" in body
    assert "matrix.lucidcove-aa9f.localhost" not in body
    assert result.get("matrix_app_recreate", {}).get("ok") is True
    assert seen.get("cmd") == ["docker", "compose", "up", "-d", "app"]
    assert seen.get("cwd") == str(tmp_path)


def test_reconcile_skips_restamp_when_dendrite_not_on_claimed(monkeypatch, tmp_path):
    """Gated / not eligible: do not rewrite .env to a homeserver Dendrite is not serving."""
    import netconfig
    env = tmp_path / ".env"
    env.write_text("MATRIX_SERVER_NAME=matrix.lucidcove-aa9f.localhost\n")
    monkeypatch.setattr(
        netconfig, "reconcile_matrix_identity",
        lambda **kw: {
            "ok": True, "virgin": True, "changed": False, "gated": True,
            "server_name": "matrix.quietgrove.lucidcove.org",
            "current_server_name": "matrix.lucidcove-aa9f.localhost",
            "message": "gated",
        })

    class _Args:
        cove_id = "lucidcove-aa9f5ad9aa5a96da"
        agents = ""
        operators = ""
        cove_dir = str(tmp_path)
        compose_dir = str(tmp_path)
        postgres_container = ""
        dendrite_container = ""

    result = {}
    set_domain._reconcile_matrix_identity(_Args(), "quietgrove.lucidcove.org", result)
    assert "matrix_env" not in result
    assert "matrix.lucidcove-aa9f.localhost" in env.read_text()
