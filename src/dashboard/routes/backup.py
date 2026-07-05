# =============================================================================
# backup.py — CF-112 routes: configure, run, status (Cove-level git backup)
# =============================================================================
# Thin admin-gated surface over src/utils/cove_backup.py. The PAT is stored via
# the feature-overrides store (pipeline_keys pattern) and NEVER echoed — the
# status reports has_token only. The onboarding "Protect your Cove" card reads
# /api/backup/status and clears on configured + first green run.
# =============================================================================
import asyncio
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.utils.cove_backup import (
    FLAG_REMOTE, FLAG_TOKEN, backup_configured, backup_green,
    get_backup_config, get_last_status, normalize_remote_url, run_cove_backup,
)

log = logging.getLogger(__name__)
router = APIRouter()

_MASK = "********"
_running = {"task": None}


@router.get("/api/backup/status")
async def backup_status(request: Request):
    """Config presence + last run. Open within the Cove (no secrets in here)."""
    cfg = get_backup_config()
    task = _running.get("task")
    return {
        "configured": backup_configured(),
        "green": backup_green(),
        "remote_url": cfg["remote_url"],
        "has_token": cfg["has_token"],
        "running": bool(task and not task.done()),
        "last": get_last_status(),
    }


class BackupConfig(BaseModel):
    remote_url: str = ""
    token: str = ""


@router.post("/api/backup/config")
async def save_backup_config(body: BackupConfig, request: Request):
    """Set the backup remote + PAT. Admin only. Masked token echo = keep existing;
    explicit empty token clears it. URL is normalized (page URL / owner-repo /
    .git all accepted)."""
    from src.dashboard.routes.settings import _is_admin_presence
    if not await _is_admin_presence(request):
        return JSONResponse(status_code=403, content={"error": "Admin Presence only"})
    url = (body.remote_url or "").strip()
    if url:
        norm = normalize_remote_url(url)
        if not norm:
            return JSONResponse(status_code=400, content={
                "error": "That doesn't look like a GitHub repo — paste the repo page URL "
                         "(https://github.com/you/your-backup-repo) or owner/repo."})
        url = norm
    token = (body.token or "").strip()
    updates = {FLAG_REMOTE: url}
    if token != _MASK:  # masked echo = leave the stored token alone
        updates[FLAG_TOKEN] = token
    from src.config import save_feature_overrides
    if not save_feature_overrides(updates):
        return JSONResponse(status_code=500, content={"error": "Could not save the backup settings."})
    log.info("backup config saved by admin (remote=%s, token %s)",
             url or "(cleared)", "kept" if token == _MASK else ("cleared" if not token else "set"))
    return await backup_status(request)


@router.post("/api/backup/run")
async def backup_run(request: Request):
    """Fire a backup now (admin). Runs in the background — poll /api/backup/status."""
    from src.dashboard.routes.settings import _is_admin_presence
    if not await _is_admin_presence(request):
        return JSONResponse(status_code=403, content={"error": "Admin Presence only"})
    if not backup_configured():
        return JSONResponse(status_code=400, content={
            "error": "Add your backup repo URL and token first."})
    task = _running.get("task")
    if task and not task.done():
        return {"ok": True, "started": False, "note": "A backup is already running."}
    _running["task"] = asyncio.create_task(run_cove_backup(trigger="manual"))
    return {"ok": True, "started": True}
