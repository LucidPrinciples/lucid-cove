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
#   GET  /api/video/jobs/{job_id}     -> {state, result, error, timestamps}
# States: queued | running | done | failed. The job registry is IN-MEMORY — a
# container restart loses in-flight jobs (accepted v1). The sync endpoints stay
# untouched for compat; the async path reuses them verbatim via a body-replay
# wrapper and flips video_pipeline._JOB_MODE so the internal httpx/model timeouts
# use the generous job caps (3600s transcribe / 1800s moments) instead of the
# short interactive caps.
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
_KIND_START_PHASE = {"transcribe": "transferring", "analyze": "analyzing"}


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


def _spawn(request: Request, body: dict, handler, kind: str) -> dict:
    job_id = uuid.uuid4().hex
    _JOBS[job_id] = {
        "state": "queued", "phase": "queued", "kind": kind, "result": None, "error": None,
        "created_at": time.time(), "started_at": None, "finished_at": None,
    }
    _prune()
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


@router.get("/jobs/{job_id}")
async def job_status(job_id: str, request: Request):
    """Poll a job. Returns its state (+ result when done, + error when failed)."""
    job = _JOBS.get(job_id)
    if not job:
        return JSONResponse({"error": "unknown job_id"}, status_code=404)
    return {"job_id": job_id, **{k: job.get(k) for k in (
        "state", "phase", "kind", "result", "error", "created_at", "started_at", "finished_at")}}
