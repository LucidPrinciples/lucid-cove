"""
Steward queue routes — the operator's window on the team's execution queue.

Steward-unit spec Pillar 1 (DB-backed). The backlog board is the operator's
INTAKE (pre-sort inbox, kept close to empty); this queue is the team's
EXECUTION surface. The board's "→ Team" button posts here; the steward works
the queue via steward_queue_tools; the operator sees status on the board page.

GET  /api/steward-queue              open items (+ recent closed with ?closed=1)
POST /api/steward-queue/add          {source, title, detail} → new queued item
POST /api/steward-queue/{id}/update  {status?, assignee?, pr_url?, notes?}

Admin-gated in multi mode (Cove-level operations); single mode = the operator.
"""

import os

from fastapi import APIRouter, HTTPException, Request

from src.tools.steward_queue_tools import VALID_STATUSES, can_transition

router = APIRouter()


async def _require_operator(request: Request):
    if os.getenv("COVE_MODE", "single") != "multi":
        return
    from src.dashboard.routes.presence import get_current_presence
    p = await get_current_presence(request)
    if not p or p.get("cove_role") != "admin":
        raise HTTPException(403, "Admin only.")


def _row_dict(r) -> dict:
    return {
        "id": r["id"], "source": r["source"], "title": r["title"],
        "detail": r["detail"], "status": r["status"], "assignee": r["assignee"],
        "pr_url": r["pr_url"], "notes": r["notes"],
        "created_at": r["created_at"].isoformat() if r["created_at"] else "",
        "updated_at": r["updated_at"].isoformat() if r["updated_at"] else "",
    }


@router.get("/api/steward-queue")
async def list_queue(request: Request, closed: int = 0):
    await _require_operator(request)
    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            if closed:
                r = await conn.execute(
                    "SELECT * FROM steward_queue "
                    "ORDER BY (status IN ('done','dropped')), updated_at DESC LIMIT 100")
            else:
                r = await conn.execute(
                    "SELECT * FROM steward_queue WHERE status NOT IN ('done','dropped') "
                    "ORDER BY created_at LIMIT 100")
            rows = await r.fetchall()
    except Exception:
        # Table not migrated yet — an empty queue, never a broken board.
        return {"items": []}
    return {"items": [_row_dict(r) for r in rows]}


@router.post("/api/steward-queue/add")
async def add_item(request: Request):
    await _require_operator(request)
    body = await request.json()
    title = (body.get("title") or "").strip()
    if not title:
        raise HTTPException(400, "title required")
    source = (body.get("source") or "").strip()[:80]
    detail = (body.get("detail") or "").strip()[:4000]

    from src.memory.database import get_db
    async with get_db() as conn:
        # Dedup: an OPEN item from the same board ref is the same work — don't
        # double-queue on a double-click or a re-flow of the same card.
        if source:
            r = await conn.execute(
                "SELECT * FROM steward_queue WHERE source = %s "
                "AND status NOT IN ('done','dropped') LIMIT 1", (source,))
            existing = await r.fetchone()
            if existing:
                return {"item": _row_dict(existing), "existing": True}
        r = await conn.execute(
            "INSERT INTO steward_queue (source, title, detail) "
            "VALUES (%s, %s, %s) RETURNING *", (source, title, detail))
        row = await r.fetchone()
    return {"item": _row_dict(row), "existing": False}


@router.post("/api/steward-queue/{item_id}/update")
async def update_item(item_id: int, request: Request):
    await _require_operator(request)
    body = await request.json()
    status = (body.get("status") or "").strip().lower()
    if status and status not in VALID_STATUSES:
        raise HTTPException(400, f"Invalid status. Valid: {', '.join(VALID_STATUSES)}")

    from src.memory.database import get_db
    async with get_db() as conn:
        r = await conn.execute(
            "SELECT status FROM steward_queue WHERE id = %s", (item_id,))
        row = await r.fetchone()
        if not row:
            raise HTTPException(404, "No such queue item")
        if status and not can_transition(row["status"], status):
            raise HTTPException(409, f"Illegal move: {row['status']} → {status}")
        sets, args = ["updated_at = NOW()"], []
        if status:
            sets.append("status = %s"); args.append(status)
            if status == "done":
                sets.append("done_at = NOW()")
        for field in ("assignee", "pr_url", "notes"):
            if field in body:
                sets.append(f"{field} = %s"); args.append((body.get(field) or "").strip()[:2000])
        args.append(item_id)
        r = await conn.execute(
            f"UPDATE steward_queue SET {', '.join(sets)} WHERE id = %s RETURNING *",
            tuple(args))
        updated = await r.fetchone()
    return {"item": _row_dict(updated)}
