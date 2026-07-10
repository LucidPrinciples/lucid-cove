# #D28 — custom runbooks from the operator's NC space. Beside the baked-in seeds,
# the route reads AgentSkills/Ops/runbooks/*.json over WebDAV and merges them into
# the list, NC winning on an id/slug collision. A runbook becomes a file both
# Chords (Mac NC sync) and Claude (Cowork mount) edit, rendered by the MC.
import json
import pathlib

import pytest

import src.dashboard.routes.runbooks as rb

REPO_RUNBOOKS = pathlib.Path(__file__).resolve().parents[1] / "runbooks"


@pytest.fixture
def _seeds_on_disk(monkeypatch):
    # point the route at the repo's shipped seed runbooks (the test env has no
    # /app/data/runbooks volume)
    monkeypatch.setattr(rb, "RUNBOOKS_DIR", REPO_RUNBOOKS)
    monkeypatch.setattr(rb, "_ensure_dir", lambda: None)


def test_merge_nc_wins_on_collision():
    # keyed by file stem — an NC file named like a seed overrides it
    seed = {"01-update-cove": {"title": "Seed", "steps": []},
            "04-backup-cove": {"title": "Backup", "steps": []}}
    nc = {"01-update-cove": {"title": "My Override", "steps": [], "source": "nc"},
          "90-my-custom": {"title": "Mine", "steps": [], "source": "nc"}}
    merged = rb._merge_runbooks(seed, nc)
    # collision → NC copy wins
    assert merged["01-update-cove"]["title"] == "My Override"
    # non-colliding seed survives, NC-only added
    assert merged["04-backup-cove"]["title"] == "Backup"
    assert merged["90-my-custom"]["title"] == "Mine"
    assert set(merged) == {"01-update-cove", "04-backup-cove", "90-my-custom"}


def test_merge_is_pure_no_mutation():
    seed = {"a": {"id": "a", "steps": []}}
    nc = {"b": {"id": "b", "steps": []}}
    rb._merge_runbooks(seed, nc)
    assert set(seed) == {"a"} and set(nc) == {"b"}  # inputs untouched


@pytest.mark.asyncio
async def test_nc_runbooks_empty_without_request():
    assert await rb._nc_runbooks(None) == {}


@pytest.mark.asyncio
async def test_nc_runbooks_empty_when_no_creds(monkeypatch):
    async def _no_creds(_req):
        return (None, None, None)
    monkeypatch.setattr("src.dashboard.routes.nextcloud.get_nc_creds", _no_creds)
    assert await rb._nc_runbooks(object()) == {}


@pytest.mark.asyncio
async def test_nc_runbooks_swallows_cred_errors(monkeypatch):
    async def _boom(_req):
        raise RuntimeError("nc down")
    monkeypatch.setattr("src.dashboard.routes.nextcloud.get_nc_creds", _boom)
    assert await rb._nc_runbooks(object()) == {}  # best-effort, never raises


@pytest.mark.asyncio
async def test_list_merges_nc_over_seed(monkeypatch, _seeds_on_disk):
    # seeds present on disk; NC contributes one override + one new runbook
    async def _restricted(_req):
        return False
    monkeypatch.setattr(rb, "_is_restricted_presence", _restricted)

    async def _nc(_req):
        return {
            "01-update-cove": {"id": "update-cove", "num": 1, "title": "Operator's Update",
                               "category": "ops", "steps": [{"label": "x", "command": "y"}],
                               "source": "nc"},
            "90-my-notes": {"id": "my-notes", "num": 42, "title": "My Notes",
                            "category": "ops", "steps": [], "source": "nc"},
        }
    monkeypatch.setattr(rb, "_nc_runbooks", _nc)

    out = await rb.list_runbooks(object())
    by = {r["slug"]: r for r in out["runbooks"]}
    # NC override wins on the colliding slug + is flagged as nc-sourced
    assert by["01-update-cove"]["name"] == "Operator's Update"
    assert by["01-update-cove"]["source"] == "nc"
    # a seed with no NC override keeps source 'seed'
    assert by["05-stop-start"]["source"] == "seed"
    # NC-only runbook appears
    assert by["90-my-notes"]["name"] == "My Notes"


@pytest.mark.asyncio
async def test_get_runbook_prefers_nc_copy(monkeypatch, _seeds_on_disk):
    async def _restricted(_req):
        return False
    monkeypatch.setattr(rb, "_is_restricted_presence", _restricted)

    async def _nc(_req):
        return {"01-update-cove": {"id": "update-cove", "title": "NC Version",
                                   "steps": [{"label": "l", "command": "cd {{COVE_DIR}} && echo hi"}],
                                   "source": "nc"}}
    monkeypatch.setattr(rb, "_nc_runbooks", _nc)
    monkeypatch.setenv("COVE_COVE_DIR", "/home/x/cove")
    monkeypatch.setattr("src.config.load_cove_config", lambda: {}, raising=False)

    data = await rb.get_runbook("01-update-cove", object())
    assert data["name"] == "NC Version"
    # path templating still applies to a custom NC runbook
    assert data["steps"][0]["command"] == "cd /home/x/cove && echo hi"
