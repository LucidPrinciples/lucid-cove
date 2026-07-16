"""Truth Gate admin API (#D57) — settings + recent fires.

Admin-gated. Backs the Intelligence-panel Truth Gate card in settings-admin.js.
"""

import os

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()

_BOOL_KEYS = (
    "truth_gate.enabled",
    "truth_gate.enabled_managers",
    "truth_gate.enabled_presences",
    "truth_gate.enabled_team",
)


async def _require_operator(request: Request):
    if os.getenv("COVE_MODE", "single") != "multi":
        return
    from src.dashboard.routes.presence import get_current_presence
    p = await get_current_presence(request)
    if not p or p.get("cove_role") != "admin":
        raise HTTPException(403, "Admin only.")


@router.get("/api/truth-gate/settings")
async def get_truth_gate_settings(request: Request):
    await _require_operator(request)
    from src.utils.settings import get_setting
    from src.graphs.truth_gate_events import DEFAULT_JUDGE_MODEL
    out = {"judge_model": await get_setting("truth_gate.judge_model", DEFAULT_JUDGE_MODEL),
           "judge_model_default": DEFAULT_JUDGE_MODEL}
    for key in _BOOL_KEYS:
        out[key.split(".", 1)[1]] = (await get_setting(key, "true")).lower() in (
            "true", "1", "yes", "on")
    return out


@router.put("/api/truth-gate/settings")
async def put_truth_gate_settings(request: Request):
    await _require_operator(request)
    from src.utils.settings import update_setting
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "JSON body required.")

    saved = {}
    if "judge_model" in body:
        model = str(body.get("judge_model") or "").strip()
        if len(model) > 200:
            raise HTTPException(400, "judge_model too long.")
        # Empty string = reset to default (store empty; getter falls back).
        await update_setting("truth_gate.judge_model", model)
        saved["judge_model"] = model
    for key in _BOOL_KEYS:
        short = key.split(".", 1)[1]
        if short in body:
            val = "true" if bool(body.get(short)) else "false"
            await update_setting(key, val)
            saved[short] = val
    if not saved:
        raise HTTPException(400, "No recognized settings in body.")
    return {"ok": True, "saved": saved}


@router.get("/api/truth-gate/events")
async def get_truth_gate_events(request: Request, limit: int = 20):
    await _require_operator(request)
    from src.graphs.truth_gate_events import recent_events
    return {"events": await recent_events(limit=limit)}
