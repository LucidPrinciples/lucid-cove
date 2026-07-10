"""
Watcher routes — the operator's window on the background monitor.

GET  /api/watcher/alerts          open alerts (renders on the Attention home)
POST /api/watcher/alerts/dismiss  {"alert_key": ...} → dismissed (stays dismissed
                                  even if the condition persists)
POST /api/watcher/run             run the checks now (admin; manual trigger)

Admin-gated in multi mode: alerts describe Cove-level operations (approvals,
queues, tunings) — member presences don't see them. Single mode = the operator.
"""

import os

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()


async def _require_operator(request: Request):
    """Multi mode: admin presences only. Single mode: pass (the operator)."""
    if os.getenv("COVE_MODE", "single") != "multi":
        return
    from src.dashboard.routes.presence import get_current_presence
    p = await get_current_presence(request)
    if not p or p.get("cove_role") != "admin":
        raise HTTPException(403, "Admin only.")


@router.get("/api/watcher/alerts")
async def get_alerts(request: Request):
    await _require_operator(request)
    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            r = await conn.execute(
                """SELECT alert_key, category, title, detail, urgency,
                          first_seen, last_seen
                   FROM watcher_alerts WHERE status = 'open'
                   ORDER BY urgency = 'high' DESC, last_seen DESC
                   LIMIT 50""")
            rows = await r.fetchall()
    except Exception:
        # Table not migrated yet / DB hiccup — an empty list, never a broken home.
        return {"alerts": []}
    return {"alerts": [{
        "alert_key": row["alert_key"],
        "category": row["category"],
        "title": row["title"],
        "detail": row["detail"],
        "urgency": row["urgency"],
        "first_seen": row["first_seen"].isoformat() if row["first_seen"] else "",
    } for row in rows]}


@router.post("/api/watcher/alerts/dismiss")
async def dismiss_alert(request: Request):
    await _require_operator(request)
    body = await request.json()
    key = (body.get("alert_key") or "").strip()
    if not key:
        raise HTTPException(400, "alert_key required")
    from src.memory.database import get_db
    async with get_db() as conn:
        r = await conn.execute(
            "UPDATE watcher_alerts SET status = 'dismissed', resolved_at = NOW() "
            "WHERE alert_key = %s AND status = 'open' RETURNING alert_key",
            (key,))
        done = await r.fetchone()
    if not done:
        raise HTTPException(404, "No open alert with that key")
    return {"dismissed": key}


@router.post("/api/watcher/run")
async def run_now(request: Request):
    await _require_operator(request)
    from src.utils.watcher import run_watcher
    return await run_watcher()
