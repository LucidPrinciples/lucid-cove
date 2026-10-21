"""In-memory Process jobs for bookkeeping Drop ingest.

POST /api/books/process starts a run and returns at once. The page polls
GET /api/books/process/{job_id}. Registry is process-local; a container
restart drops in-flight jobs. Never stores statement text or filenames.
"""

from __future__ import annotations

import time
import uuid

_JOBS: dict[str, dict] = {}
_TASKS: set = set()
_MAX_JOBS = 40
_VALID_STATES = ("queued", "running", "done", "failed")
_ACTIVE_STATES = ("queued", "running")


def _prune() -> None:
    if len(_JOBS) <= _MAX_JOBS:
        return
    finished = sorted(
        (jid for jid, job in _JOBS.items() if job.get("state") in ("done", "failed")),
        key=lambda jid: _JOBS[jid].get("finished_at") or 0,
    )
    for jid in finished[: len(_JOBS) - _MAX_JOBS]:
        _JOBS.pop(jid, None)


def create_process_job(*, actor_id: str, path: str, files: int) -> dict:
    job_id = uuid.uuid4().hex
    now = time.time()
    job = {
        "job_id": job_id,
        "actor_id": str(actor_id or ""),
        "path": path,
        "state": "queued",
        "files": int(files or 0),
        "file_index": 0,
        "added": 0,
        "skipped": 0,
        "pending": 0,
        "processed": 0,
        "ocr": 0,
        "model_read": 0,
        "stamped": 0,
        "unread_reason": None,
        "account": "",
        "drop_folder": "",
        "filing_book": "",
        "needs_review_count": None,
        "error": None,
        "created_at": now,
        "started_at": None,
        "finished_at": None,
    }
    _JOBS[job_id] = job
    _prune()
    return job


def retain_task(task) -> None:
    """Keep a strong ref so asyncio.create_task is not GC'd mid-run."""
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


def find_active_process_job(path: str) -> dict | None:
    want = (path or "").strip()
    if not want:
        return None
    for job in _JOBS.values():
        if job.get("path") == want and job.get("state") in _ACTIVE_STATES:
            return job
    return None


def get_process_job(job_id: str) -> dict | None:
    return _JOBS.get(job_id)


def patch_process_job(job_id: str, **fields) -> dict | None:
    job = _JOBS.get(job_id)
    if job is None:
        return None
    if "state" in fields and fields["state"] not in _VALID_STATES:
        fields = {k: v for k, v in fields.items() if k != "state"}
    job.update(fields)
    return job


def public_process_job(job: dict) -> dict:
    """Counts and progress only — no statement names."""
    state = job.get("state") or "queued"
    files = int(job.get("files") or 0)
    file_index = int(job.get("file_index") or 0)
    pending = int(job.get("pending") or 0)
    return {
        "ok": state != "failed",
        "job_id": job.get("job_id"),
        "state": state,
        "path": job.get("path") or "",
        "account": job.get("account") or "",
        "drop_folder": job.get("drop_folder") or "",
        "filing_book": job.get("filing_book") or "",
        "files": files,
        "file_index": file_index,
        "added": int(job.get("added") or 0),
        "skipped": int(job.get("skipped") or 0),
        "pending": pending,
        "processed": int(job.get("processed") or 0),
        "stamped": int(job.get("stamped") or 0),
        "ocr": int(job.get("ocr") or 0),
        "model_read": int(job.get("model_read") or 0),
        "needs_review_count": job.get("needs_review_count"),
        "unread_reason": job.get("unread_reason"),
        "remaining": pending if state == "running" else 0,
        "more_pending": False,
        "error": job.get("error"),
    }
