"""Cove Charter admin API (#D58) — mission + operating principles.

The Charter is the Cove-level directive injected into every agent's system
prompt (identity.py::_charter_block). Storage = system_settings keys
charter.mission / charter.principles (seeded by migration 038; the install
wizard writes mission at finalize). Admin-gated; backs the Charter card in
Cove Settings (settings-account.js).
"""

import os

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()

_MISSION_MAX = 500
_PRINCIPLES_MAX = 4000


async def _require_operator(request: Request):
    if os.getenv("COVE_MODE", "single") != "multi":
        return
    from src.dashboard.routes.presence import get_current_presence
    p = await get_current_presence(request)
    if not p or p.get("cove_role") != "admin":
        raise HTTPException(403, "Admin only.")


@router.get("/api/charter")
async def get_charter(request: Request):
    await _require_operator(request)
    from src.utils.settings import get_setting
    return {
        "mission": await get_setting("charter.mission", ""),
        "principles": await get_setting("charter.principles", ""),
    }


@router.put("/api/charter")
async def put_charter(request: Request):
    await _require_operator(request)
    from src.utils.settings import update_setting
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "JSON body required.")

    saved = {}
    if "mission" in body:
        mission = str(body.get("mission") or "").strip()
        if len(mission) > _MISSION_MAX:
            raise HTTPException(400, f"Mission too long (max {_MISSION_MAX} chars).")
        await update_setting("charter.mission", mission)
        saved["mission"] = mission
    if "principles" in body:
        principles = str(body.get("principles") or "").strip()
        if len(principles) > _PRINCIPLES_MAX:
            raise HTTPException(400, f"Principles too long (max {_PRINCIPLES_MAX} chars).")
        await update_setting("charter.principles", principles)
        saved["principles"] = principles
    if not saved:
        raise HTTPException(400, "Nothing to save — send mission and/or principles.")
    return {"ok": True, "saved": list(saved.keys())}
