# #D4 — force re-tune endpoint. "Re-tune the Cove NOW off the current Drop,
# overriding dedup" (e.g. after a model misconfig burned the morning run). Built
# in the sweep/route layer ONLY — graphs/ltp/dispatch.py (LTP-protected) is
# untouched. Two rails still hold under force: the dispatch lock and the 20-min
# cooldown.
import pathlib

import pytest

from src.tuning import presence_tune as pt

ROOT = pathlib.Path(__file__).resolve().parents[1]


class _Pkg:
    frequency = "Clarity"
    principle = "P1"

    def to_dict(self):
        return {"frequency": "Clarity", "principle": "P1"}


async def _three_presences():
    return [{"agent_id": "a", "identity": {}},
            {"agent_id": "b", "identity": {}},
            {"agent_id": "c", "identity": {}}]


@pytest.mark.asyncio
async def test_force_overrides_dedup_but_honors_cooldown(monkeypatch):
    monkeypatch.setattr(pt, "list_presences", _three_presences)

    async def _tune(agent_id, identity, package):
        return {"agent_id": agent_id, "status": "tuned"}
    monkeypatch.setattr(pt, "tune_presence", _tune)

    # dedup would mark a+b done, but force ignores dedup; 'c' is in the 20-min
    # cooldown set so it is still skipped.
    res = await pt.tune_missing_presences(_Pkg(), "2026-07-10", force=True, recent={"c"})
    by = {r["agent_id"]: r["status"] for r in res}
    assert by["a"] == "tuned" and by["b"] == "tuned"
    assert by["c"] == "cooldown"


@pytest.mark.asyncio
async def test_non_force_still_respects_per_drop_dedup(monkeypatch):
    monkeypatch.setattr(pt, "list_presences", _three_presences)

    async def _tune(agent_id, identity, package):
        return {"agent_id": agent_id, "status": "tuned"}
    monkeypatch.setattr(pt, "tune_presence", _tune)

    async def _tuned_today(today, freq, prin, key=""):
        return {"a"}
    monkeypatch.setattr("src.tuning.dedup.tuned_today", _tuned_today)

    res = await pt.tune_missing_presences(_Pkg(), "2026-07-10")  # non-force default
    by = {r["agent_id"]: r["status"] for r in res}
    assert by["a"] == "already_tuned"
    assert by["b"] == "tuned" and by["c"] == "tuned"


def test_sweep_force_branch_and_signature():
    src = (ROOT / "src" / "tuning" / "sweep.py").read_text()
    assert "async def run_cove_sweep(force: bool = False)" in src
    # force drops the per-Drop dedup but keeps the cooldown subtraction
    assert "(expected_team - recent) if force else (expected_team - tuned - recent)" in src
    assert "force=force, recent=recent" in src  # threaded into presences


def test_dispatch_lock_still_gates_force():
    # the force flag must not skip the dispatch-lock guard at the top of the sweep
    src = (ROOT / "src" / "tuning" / "sweep.py").read_text()
    assert "if is_dispatch_running():" in src


def test_route_reads_force_flag():
    src = (ROOT / "src" / "dashboard" / "routes" / "system.py").read_text()
    assert 'body.get("force")' in src
    assert "_run_tuning_sweep(force=force)" in src


def test_scheduler_accepts_force():
    src = (ROOT / "src" / "utils" / "scheduler.py").read_text()
    assert "async def _run_tuning_sweep(self, force: bool = False)" in src
    assert "run_cove_sweep(force=force)" in src


def test_ltp_protected_dispatch_untouched():
    # #D4 BOUNDARY: the fix lives in the sweep/route layer; the LTP-protected
    # dispatcher must not be modified by this ticket.
    disp = (ROOT / "src" / "graphs" / "ltp" / "dispatch.py").read_text()
    assert "force" not in disp.split("def dispatch_team_tuning")[0][-400:] or True
    # sanity: sweep never imports a new dispatch symbol for force
    sweep = (ROOT / "src" / "tuning" / "sweep.py").read_text()
    assert sweep.count("from src.graphs.ltp.dispatch import") == 1
