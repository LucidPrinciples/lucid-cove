# #D21 — health-checks runbook shipped in the product. runbooks/06-health-checks.json
# renders in every MC System tab as copy-paste steps (delegation, watcher,
# approvals, tuning), same schema as 01-05, templated {{COVE_DIR}} + compose
# service names so it works on any box. Founder's filled reference = RB16.
import json
import pathlib

import src.dashboard.routes.runbooks as rb

RB = pathlib.Path(__file__).resolve().parents[1] / "runbooks" / "06-health-checks.json"
DATA = json.loads(RB.read_text())


def test_schema_matches_the_shipped_runbooks():
    assert DATA["id"] == "health-checks"
    assert DATA["num"] == 6
    assert DATA["title"]
    assert DATA["category"] == "ops"
    assert DATA["audience"] == "steward"  # ops, hidden from a non-admin presence
    assert len(DATA["steps"]) == 4
    for s in DATA["steps"]:
        assert s.get("label") and s.get("command")


def test_covers_the_four_rb16_checks():
    blob = json.dumps(DATA).lower()
    assert "delegation" in blob
    assert "watcher_alerts" in blob
    assert "approval_requests" in blob
    assert "ltp-dispatch" in blob or "tuning sweep" in blob.lower()


def test_uses_portable_templating_not_hardcoded_containers():
    cmds = " ".join(s["command"] for s in DATA["steps"])
    assert "{{COVE_DIR}}" in cmds
    assert "docker compose" in cmds
    # never hardcode a specific box's container/db names
    assert "lucidcove-6f6f" not in cmds
    assert "clearfield-postgres" not in cmds


def test_db_env_expands_inside_the_container_not_the_host():
    # the psql steps read PGUSER/PGDATABASE from POSTGRES_* — that MUST happen
    # inside the postgres container, so the arg is single-quoted (literal to the
    # host shell) with the SQL double-quoted inside.
    for s in DATA["steps"]:
        c = s["command"]
        if "POSTGRES_USER" in c:
            assert "sh -c '" in c  # single-quoted -> host does not expand $POSTGRES_USER
            assert "PGUSER=$POSTGRES_USER" in c
            # no single-quoted SQL literal that would collide with the outer quotes
            assert "psql -c \"" in c


def test_it_is_read_only():
    # health checks never mutate: only logs/grep/SELECT, never insert/update/delete/restart
    blob = " ".join(s["command"] for s in DATA["steps"]).lower()
    for danger in ("insert ", "update ", "delete ", "drop ", "restart", "compose up", "compose down"):
        assert danger not in blob


def test_fill_paths_resolves_cove_dir(monkeypatch):
    # the route's templating fills {{COVE_DIR}} for this runbook like any other
    for v in ("COVE_HOST_DIR", "COVE_CLONE_DIR", "COVE_COVE_DIR"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("COVE_COVE_DIR", "/home/x/mycove")
    monkeypatch.setattr("src.config.load_cove_config", lambda: {}, raising=False)
    filled = rb._fill_paths(json.loads(RB.read_text()))
    assert all("{{COVE_DIR}}" not in s["command"] for s in filled["steps"])
    assert any("/home/x/mycove" in s["command"] for s in filled["steps"])
    assert not filled.get("paths_incomplete")
