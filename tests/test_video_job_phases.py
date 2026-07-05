"""batch8 #4 — video jobs expose a coarse progress PHASE. Async, in-memory."""
import asyncio
import pytest

from src.dashboard.routes import video_jobs as vj
from src.dashboard.routes.video_jobs import set_phase, VALID_PHASES


@pytest.fixture(autouse=True)
def _clean_registry():
    vj._JOBS.clear()
    yield
    vj._JOBS.clear()


def test_spawn_starts_queued():
    async def _h(_req):
        await asyncio.sleep(0.5)  # keep it queued/running long enough to observe
        return {"ok": True}

    async def _go():
        out = vj._spawn(request=None, body={}, handler=_h, kind="transcribe")
        # Immediately after spawn, before the task gets to run: phase == queued.
        assert vj._JOBS[out["job_id"]]["phase"] == "queued"
        assert out["state"] == "queued"

    asyncio.run(_go())


def test_running_gets_kind_start_phase_then_done():
    started = {}

    async def _h(_req):
        # While running, the transcribe job should report "transferring".
        jid = list(vj._JOBS)[0]
        started["phase"] = vj._JOBS[jid]["phase"]
        return {"ok": True}

    async def _go():
        out = vj._spawn(request=None, body={}, handler=_h, kind="transcribe")
        for _ in range(50):
            if vj._JOBS[out["job_id"]]["state"] in ("done", "failed"):
                break
            await asyncio.sleep(0.01)
        return out["job_id"]

    jid = asyncio.run(_go())
    assert started["phase"] == "transferring"
    assert vj._JOBS[jid]["phase"] == "done"
    assert vj._JOBS[jid]["state"] == "done"


def test_analyze_kind_starts_analyzing():
    seen = {}

    async def _h(_req):
        seen["phase"] = vj._JOBS[list(vj._JOBS)[0]]["phase"]
        return {"ok": True}

    async def _go():
        out = vj._spawn(request=None, body={}, handler=_h, kind="analyze")
        for _ in range(50):
            if vj._JOBS[out["job_id"]]["state"] != "queued" and \
                    vj._JOBS[out["job_id"]]["state"] != "running":
                break
            await asyncio.sleep(0.01)
        return out
    asyncio.run(_go())
    assert seen["phase"] == "analyzing"


def test_failed_job_phase_is_failed():
    async def _h(_req):
        raise RuntimeError("boom")

    async def _go():
        out = vj._spawn(request=None, body={}, handler=_h, kind="transcribe")
        for _ in range(50):
            if vj._JOBS[out["job_id"]]["state"] == "failed":
                break
            await asyncio.sleep(0.01)
        return out["job_id"]

    jid = asyncio.run(_go())
    assert vj._JOBS[jid]["phase"] == "failed"


def test_set_phase_noop_outside_job():
    # No current job → never raises, never mutates.
    set_phase("rendering")  # should be a silent no-op
    assert vj._CURRENT_JOB.get() is None


def test_set_phase_rejects_unknown_phase():
    vj._JOBS["x"] = {"state": "running", "kind": "transcribe", "phase": "transferring"}
    tok = vj._CURRENT_JOB.set("x")
    try:
        set_phase("bogus")
        assert vj._JOBS["x"]["phase"] == "transferring"  # unchanged
        set_phase("rendering")
        assert vj._JOBS["x"]["phase"] == "rendering"
    finally:
        vj._CURRENT_JOB.reset(tok)


def test_valid_phases_complete():
    for p in ("queued", "transferring", "transcribing", "analyzing",
              "rendering", "done", "failed"):
        assert p in VALID_PHASES
