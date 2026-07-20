# =============================================================================
# video_jobs.py — A14: async video pipeline jobs (transcribe + analyze).
# =============================================================================
# The sync /api/video/transcribe and /analyze routes hold the HTTP request open
# for the whole job. A normal 787MB / 2-min phone video 504'd that held-open
# request while the P620 job actually SUCCEEDED; the standard case is 20min /
# 4-5GB and a local-brain moments pass needs minutes. This module runs the SAME
# route logic as a background asyncio task and hands the browser a job_id to poll.
#
# Design (locked):
#   POST /api/video/transcribe/start  {filename}                       -> {job_id}
#   POST /api/video/analyze/start     {stem|transcript_file, presence_name} -> {job_id}
#   POST /api/video/caption-full/start {stem, caption?, ...}           -> {job_id}  (#MESH-n/a / A14)
#   POST /api/video/process-moments/start {stem, moments, ...}         -> {job_id}
#   GET  /api/video/jobs/{job_id}     -> {state, result, error, timestamps}
# States: queued | running | done | failed. The job registry is IN-MEMORY — a
# container restart loses in-flight jobs (accepted v1). The sync endpoints stay
# untouched for compat; the async path reuses them verbatim via a body-replay
# wrapper and flips video_pipeline._JOB_MODE so the internal httpx/model timeouts
# use the generous job caps (3600s transcribe / 1800s moments) instead of the
# short interactive caps.
#
# Caption-full / process-moments were still sync held-open POSTs — long ffmpeg
# runs left the browser with an empty body ("Unexpected end of JSON input") while
# the render may still have succeeded server-side. Same A14 pattern as transcribe.
#
# Repo rule: new feature area → new file.
# =============================================================================
import asyncio
import contextvars
import json
import logging
import time
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/video", tags=["video-jobs"])

# job_id -> {state, phase, kind, result, error, created_at, started_at, finished_at}
_JOBS: dict = {}
_MAX_JOBS = 200  # keep the newest N; prune finished jobs beyond that (in-memory v1)

# A14 #4 — coarse progress PHASE, distinct from `state` (queued/running/done/failed).
# A multi-minute mesh transfer looked identical to a hang behind a bare spinner;
# the phase names what the job is actually doing right now. Set app-side at the
# natural transition points (around the pipecat call, around the model call). No
# percent bars — phases only.
VALID_PHASES = ("queued", "transferring", "transcribing", "analyzing",
                "rendering", "done", "failed")

# Carries the running job's id into the reused sync handler so it can report a
# finer phase via set_phase(). Unset outside a job context → set_phase is a no-op,
# so the plain sync /transcribe and /analyze endpoints are unaffected.
_CURRENT_JOB: contextvars.ContextVar = contextvars.ContextVar(
    "current_video_job", default=None)

# The working phase each job KIND starts in when it begins running.
_KIND_START_PHASE = {
    "transcribe": "transferring",
    "analyze": "analyzing",
    "caption_full": "rendering",
    "process_moments": "rendering",
}


def set_phase(phase: str) -> None:
    """Update the current background job's progress phase. No-op when not running
    inside a job (the sync endpoints) or for an unknown phase — never raises into
    the pipeline hot path."""
    if phase not in VALID_PHASES:
        return
    jid = _CURRENT_JOB.get()
    job = _JOBS.get(jid) if jid else None
    if job is not None and job.get("state") == "running":
        job["phase"] = phase


class _ReplayRequest:
    """Wraps a live Request so the existing sync handler can be re-invoked in the
    background: json() returns the already-parsed body (the stream is consumed),
    every other attribute (cookies/headers/scope for presence + NC creds) proxies
    to the real request, which the job holds a reference to for its lifetime."""

    def __init__(self, request: Request, body: dict):
        self._request = request
        self._body = body

    async def json(self):
        return self._body

    async def body(self):
        return json.dumps(self._body).encode()

    def __getattr__(self, name):
        return getattr(self._request, name)


# ── #D39: durability ─────────────────────────────────────────────────────────
# The in-memory registry lost in-flight jobs on any restart, so a job that
# finished in pipecat showed the browser an error at the end (its id 404'd after
# the restart). We mirror each job's lightweight STATE to the video_jobs table
# (never the result payload) and, on boot, orphan-mark still-running rows to
# 'failed' so a polling browser gets the truth. All best-effort — a DB hiccup
# must never break the pipeline hot path.

_PERSIST_COLS = ("state", "phase", "kind", "error",
                 "created_at", "started_at", "finished_at")


async def _persist_job(job_id: str) -> None:
    """Best-effort upsert of an in-memory job's state to the durable table."""
    job = _JOBS.get(job_id)
    if not job:
        return
    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            await conn.execute(
                """INSERT INTO video_jobs
                       (job_id, kind, state, phase, error,
                        created_at, started_at, finished_at, updated_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s, NOW())
                   ON CONFLICT (job_id) DO UPDATE SET
                       kind=EXCLUDED.kind, state=EXCLUDED.state,
                       phase=EXCLUDED.phase, error=EXCLUDED.error,
                       started_at=EXCLUDED.started_at,
                       finished_at=EXCLUDED.finished_at, updated_at=NOW()""",
                (job_id, job.get("kind") or "", job.get("state") or "queued",
                 job.get("phase") or "queued", str(job.get("error") or ""),
                 job.get("created_at"), job.get("started_at"),
                 job.get("finished_at")))
    except Exception as e:
        log.debug("video job persist failed for %s: %s", job_id, e)


def _persist_soon(job_id: str) -> None:
    """Fire-and-forget persist from a sync context (never awaited)."""
    try:
        asyncio.create_task(_persist_job(job_id))
    except RuntimeError:
        pass  # no running loop (e.g. under sync tests) — nothing in flight to lose


async def _load_job_row(job_id: str) -> dict | None:
    """Read a durable job row (post-restart fallback). Best-effort → None."""
    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            r = await conn.execute(
                "SELECT state, phase, kind, error, created_at, started_at, "
                "finished_at FROM video_jobs WHERE job_id = %s", (job_id,))
            row = await r.fetchone()
        return dict(row) if row else None
    except Exception as e:
        log.debug("video job load failed for %s: %s", job_id, e)
        return None


async def sweep_orphaned_video_jobs() -> int:
    """Boot recovery (#D39). A background job killed by THIS restart can't finish
    or report — its durable row is left queued/running and the browser polls it
    forever. Mark every such row 'failed' with an honest error so the UI stops
    waiting on a job that will never complete. Returns the count. Never raises."""
    from src.memory.database import get_db
    msg = ("interrupted by a restart — the app stopped mid-job; the output may "
           "still have been produced, re-run if it is missing")
    try:
        async with get_db() as conn:
            r = await conn.execute(
                "UPDATE video_jobs SET state = 'failed', phase = 'failed', "
                "error = CASE WHEN error IS NULL OR error = '' THEN %s ELSE error END, "
                "finished_at = COALESCE(finished_at, extract(epoch from now())), "
                "updated_at = NOW() "
                "WHERE state IN ('queued','running') RETURNING job_id",
                (msg,))
            rows = [x["job_id"] for x in await r.fetchall()]
    except Exception as e:
        log.warning("orphaned video-job sweep failed: %s", e)
        return 0
    if rows:
        log.info("swept %d restart-orphaned video job(s) -> failed", len(rows))
    return len(rows)


def _prune():
    if len(_JOBS) <= _MAX_JOBS:
        return
    # Drop the oldest FINISHED jobs first; never evict a running/queued one.
    finished = sorted(
        (jid for jid, j in _JOBS.items() if j["state"] in ("done", "failed")),
        key=lambda jid: _JOBS[jid].get("finished_at") or 0,
    )
    for jid in finished[: len(_JOBS) - _MAX_JOBS]:
        _JOBS.pop(jid, None)


def _payload_of(resp) -> dict:
    """Extract the JSON dict from a JSONResponse (or pass a dict through)."""
    if isinstance(resp, dict):
        return resp
    body = getattr(resp, "body", None)
    if body is None:
        return {}
    try:
        return json.loads(body)
    except Exception:
        return {"raw": body.decode("utf-8", "replace") if isinstance(body, bytes) else str(body)}


async def _execute(job_id: str, handler, request: Request, body: dict):
    """Run the sync handler in job context and record the outcome."""
    from src.dashboard.routes.video_pipeline import _JOB_MODE
    _JOB_MODE.set(True)  # generous internal timeouts for this task's context
    _CURRENT_JOB.set(job_id)  # so the handler can report a finer phase (#4)
    job = _JOBS[job_id]
    job["state"] = "running"
    job["phase"] = _KIND_START_PHASE.get(job.get("kind"), "queued")
    job["started_at"] = time.time()
    await _persist_job(job_id)  # #D39: durable 'running' so a restart can orphan-mark it
    try:
        resp = await handler(_ReplayRequest(request, body))
        status = getattr(resp, "status_code", 200)
        payload = _payload_of(resp)
        job["result"] = payload
        if status >= 400 or (isinstance(payload, dict) and payload.get("error")):
            job["state"] = "failed"
            job["phase"] = "failed"
            job["error"] = (payload.get("error") if isinstance(payload, dict) else None) or f"HTTP {status}"
        else:
            job["state"] = "done"
            job["phase"] = "done"
    except Exception as e:
        log.exception("video job %s failed", job_id)
        job["state"] = "failed"
        job["phase"] = "failed"
        job["error"] = str(e)
    finally:
        job["finished_at"] = time.time()
        await _persist_job(job_id)  # #D39: durable terminal state (done/failed)


def _spawn(request: Request, body: dict, handler, kind: str) -> dict:
    job_id = uuid.uuid4().hex
    _JOBS[job_id] = {
        "state": "queued", "phase": "queued", "kind": kind, "result": None, "error": None,
        "created_at": time.time(), "started_at": None, "finished_at": None,
    }
    _prune()
    _persist_soon(job_id)  # #D39: durable 'queued' before the turn starts
    asyncio.create_task(_execute(job_id, handler, request, body))
    return {"job_id": job_id, "state": "queued"}


@router.post("/transcribe/start")
async def start_transcribe(request: Request):
    """Kick off transcription as a background job. Same body as /transcribe."""
    body = await request.json()
    if not (body.get("filename") or "").strip():
        return JSONResponse({"error": "No filename provided"}, status_code=400)
    from src.dashboard.routes.video_pipeline import trigger_transcription
    return _spawn(request, body, trigger_transcription, "transcribe")


@router.post("/analyze/start")
async def start_analyze(request: Request):
    """Kick off moments analysis as a background job. Same body as /analyze."""
    body = await request.json()
    if not (body.get("presence_name") or "").strip():
        return JSONResponse(
            {"error": "presence_name required — identifies whose video data to write to"},
            status_code=400,
        )
    from src.dashboard.routes.video_pipeline import analyze_transcript
    return _spawn(request, body, analyze_transcript, "analyze")


@router.post("/caption-full/start")
async def start_caption_full(request: Request):
    """Kick off captioned full-length render as a background job.

    Same body as POST /api/video/caption-full. Long ffmpeg + metadata must not
    hold the browser request open (empty JSON body / gateway cut).
    """
    body = await request.json()
    if not (body.get("stem") or "").strip():
        return JSONResponse({"error": "stem required"}, status_code=400)
    from src.dashboard.routes.video_processing import caption_full_video
    return _spawn(request, body, caption_full_video, "caption_full")


@router.post("/process-moments/start")
async def start_process_moments(request: Request):
    """Kick off clip batch render as a background job. Same body as /process-moments."""
    body = await request.json()
    if not (body.get("stem") or "").strip() or not body.get("moments"):
        return JSONResponse({"error": "stem and moments required"}, status_code=400)
    from src.dashboard.routes.video_processing import process_moments
    return _spawn(request, body, process_moments, "process_moments")


@router.get("/jobs/{job_id}")
async def job_status(job_id: str, request: Request):
    """Poll a job. Returns its state (+ result when done, + error when failed)."""
    job = _JOBS.get(job_id)
    if job:
        return {"job_id": job_id, **{k: job.get(k) for k in (
            "state", "phase", "kind", "result", "error",
            "created_at", "started_at", "finished_at")}}
    # #D39: in-memory job gone (app restarted mid-poll). Fall back to the durable
    # row so the browser gets an honest state — the boot sweep has already turned a
    # killed job into 'failed' — instead of a 404 that reads as "job vanished".
    row = await _load_job_row(job_id)
    if row:
        return {"job_id": job_id, "result": None, "persisted": True, **row}
    return JSONResponse({"error": "unknown job_id"}, status_code=404)
