"""
Creation Framework API — intentional creation actions through the LP mechanism.

Each creation action moves through stages that mirror how ideas become reality:
  Broadcast → Tune → Act → Receive → Manifest → Complete

This is an optional layer on top of standard tasks. Not every action needs it.
When it's used, the framework gives the action its intentional architecture.
"""

import json
from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.requests import Request
from fastapi.responses import JSONResponse

router = APIRouter()

VALID_STAGES = ["broadcast", "tune", "act", "receive", "manifest", "complete"]
STAGE_ORDER = {s: i for i, s in enumerate(VALID_STAGES)}

VALID_FREQUENCIES = [
    "peace", "clarity", "momentum", "trust", "joy", "connection",
    "presence", "resilience", "courage", "gratitude", "release",
    "integration", "boundary",
]


# ── List active creation actions ─────────────────────────────────────────────

@router.get("/api/creation/actions")
async def list_creation_actions(include_complete: bool = False):
    """Return all active creation actions, ordered by stage progress then date."""
    from src.memory.database import get_db

    try:
        async with get_db() as conn:
            where = "WHERE archived = FALSE"
            if not include_complete:
                where += " AND stage != 'complete'"

            result = await conn.execute(
                f"""SELECT id, title, intention, frequency, tuning_key, stage,
                           tuning_notes, signs_log, manifest_notes, project_id,
                           created_by, created_at, updated_at
                    FROM creation_actions
                    {where}
                    ORDER BY
                        CASE stage
                            WHEN 'act' THEN 0
                            WHEN 'tune' THEN 1
                            WHEN 'broadcast' THEN 2
                            WHEN 'receive' THEN 3
                            WHEN 'manifest' THEN 4
                            WHEN 'complete' THEN 5
                        END,
                        updated_at DESC"""
            )
            rows = await result.fetchall()

            actions = []
            for row in rows:
                actions.append({
                    "id": row["id"],
                    "title": row["title"],
                    "intention": row["intention"],
                    "frequency": row["frequency"],
                    "tuning_key": row["tuning_key"],
                    "stage": row["stage"],
                    "signs_count": len(row["signs_log"]) if isinstance(row["signs_log"], list) else 0,
                    "project_id": row["project_id"],
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                    "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
                })

            return {"actions": actions, "count": len(actions)}

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Get single creation action detail ────────────────────────────────────────

@router.get("/api/creation/actions/{action_id}")
async def get_creation_action(action_id: int):
    """Full detail for a single creation action."""
    from src.memory.database import get_db

    try:
        async with get_db() as conn:
            result = await conn.execute(
                """SELECT id, title, intention, frequency, tuning_key, stage,
                          tuning_notes, signs_log, manifest_notes, project_id,
                          created_by, archived, created_at, updated_at
                   FROM creation_actions WHERE id = %s""",
                (action_id,),
            )
            row = await result.fetchone()
            if not row:
                return JSONResponse({"error": "not found"}, status_code=404)

            return {
                "id": row["id"],
                "title": row["title"],
                "intention": row["intention"],
                "frequency": row["frequency"],
                "tuning_key": row["tuning_key"],
                "stage": row["stage"],
                "tuning_notes": row["tuning_notes"] if isinstance(row["tuning_notes"], dict) else json.loads(row["tuning_notes"] or "{}"),
                "signs_log": row["signs_log"] if isinstance(row["signs_log"], list) else json.loads(row["signs_log"] or "[]"),
                "manifest_notes": row["manifest_notes"] if isinstance(row["manifest_notes"], dict) else json.loads(row["manifest_notes"] or "{}"),
                "project_id": row["project_id"],
                "created_by": row["created_by"],
                "archived": row["archived"],
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
            }

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Create a new creation action ─────────────────────────────────────────────

@router.post("/api/creation/actions")
async def create_creation_action(request: Request):
    """Create a new creation action. Starts at Broadcast stage.

    Body:
        title: str (required)
        intention: str — what is being created
        frequency: str — one of 13 broadcast frequencies
        tuning_key: str — canon phrase anchor
        project_id: int (optional) — link to existing project
    """
    from src.memory.database import get_db

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    title = (body.get("title") or "").strip()
    if not title:
        return JSONResponse({"error": "title is required"}, status_code=400)

    frequency = (body.get("frequency") or "").strip().lower()
    if frequency and frequency not in VALID_FREQUENCIES:
        return JSONResponse({"error": f"Invalid frequency: {frequency}"}, status_code=400)

    try:
        async with get_db() as conn:
            result = await conn.execute(
                """INSERT INTO creation_actions
                       (title, intention, frequency, tuning_key, stage, project_id, created_by)
                   VALUES (%s, %s, %s, %s, 'broadcast', %s, 'operator')
                   RETURNING id, created_at""",
                (
                    title,
                    body.get("intention"),
                    frequency or None,
                    body.get("tuning_key"),
                    body.get("project_id"),
                ),
            )
            row = await result.fetchone()

            return {
                "id": row["id"],
                "title": title,
                "stage": "broadcast",
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            }

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Update a creation action ─────────────────────────────────────────────────

@router.patch("/api/creation/actions/{action_id}")
async def update_creation_action(action_id: int, request: Request):
    """Update fields on a creation action. Accepts partial updates.

    Body (all optional):
        title, intention, frequency, tuning_key, tuning_notes, manifest_notes, project_id
    """
    from src.memory.database import get_db

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    updates = []
    params = []

    for field in ["title", "intention", "tuning_key"]:
        if field in body:
            updates.append(f"{field} = %s")
            params.append(body[field])

    if "frequency" in body:
        freq = (body["frequency"] or "").strip().lower()
        if freq and freq not in VALID_FREQUENCIES:
            return JSONResponse({"error": f"Invalid frequency: {freq}"}, status_code=400)
        updates.append("frequency = %s")
        params.append(freq or None)

    for json_field in ["tuning_notes", "manifest_notes"]:
        if json_field in body:
            updates.append(f"{json_field} = %s::jsonb")
            params.append(json.dumps(body[json_field]))

    if "project_id" in body:
        updates.append("project_id = %s")
        params.append(body["project_id"])

    if not updates:
        return JSONResponse({"error": "No fields to update"}, status_code=400)

    updates.append("updated_at = NOW()")
    params.append(action_id)

    try:
        async with get_db() as conn:
            result = await conn.execute(
                f"""UPDATE creation_actions
                    SET {', '.join(updates)}
                    WHERE id = %s AND archived = FALSE
                    RETURNING id, stage, updated_at""",
                tuple(params),
            )
            row = await result.fetchone()
            if not row:
                return JSONResponse({"error": "not found"}, status_code=404)

            return {
                "id": row["id"],
                "stage": row["stage"],
                "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
            }

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Advance to next stage ────────────────────────────────────────────────────

@router.patch("/api/creation/actions/{action_id}/stage")
async def advance_stage(action_id: int, request: Request):
    """Transition a creation action to the next stage.

    Body:
        stage: str — target stage (must be the next stage in sequence)
        force: bool (optional) — allow jumping stages (default false)
    """
    from src.memory.database import get_db

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    target = (body.get("stage") or "").strip().lower()
    if target not in VALID_STAGES:
        return JSONResponse({"error": f"Invalid stage: {target}"}, status_code=400)

    force = body.get("force", False)

    try:
        async with get_db() as conn:
            result = await conn.execute(
                "SELECT id, stage FROM creation_actions WHERE id = %s AND archived = FALSE",
                (action_id,),
            )
            row = await result.fetchone()
            if not row:
                return JSONResponse({"error": "not found"}, status_code=404)

            current = row["stage"]
            current_idx = STAGE_ORDER.get(current, 0)
            target_idx = STAGE_ORDER.get(target, 0)

            if not force and target_idx != current_idx + 1:
                return JSONResponse(
                    {"error": f"Cannot jump from '{current}' to '{target}'. Next stage is '{VALID_STAGES[current_idx + 1]}'."},
                    status_code=400,
                )

            if target_idx <= current_idx and not force:
                return JSONResponse(
                    {"error": f"Cannot go backwards from '{current}' to '{target}'."},
                    status_code=400,
                )

            await conn.execute(
                """UPDATE creation_actions
                   SET stage = %s, updated_at = NOW()
                   WHERE id = %s""",
                (target, action_id),
            )

            return {"id": action_id, "stage": target, "previous": current}

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Log a Sign observation ───────────────────────────────────────────────────

@router.post("/api/creation/actions/{action_id}/signs")
async def log_sign(action_id: int, request: Request):
    """Append a Sign observation to a creation action's signs log.

    Body:
        text: str — what was noticed
    """
    from src.memory.database import get_db

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    text = (body.get("text") or "").strip()
    if not text:
        return JSONResponse({"error": "text is required"}, status_code=400)

    sign = {
        "text": text,
        "logged_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        async with get_db() as conn:
            result = await conn.execute(
                """UPDATE creation_actions
                   SET signs_log = signs_log || %s::jsonb,
                       updated_at = NOW()
                   WHERE id = %s AND archived = FALSE
                   RETURNING id, signs_log""",
                (json.dumps([sign]), action_id),
            )
            row = await result.fetchone()
            if not row:
                return JSONResponse({"error": "not found"}, status_code=404)

            signs = row["signs_log"] if isinstance(row["signs_log"], list) else json.loads(row["signs_log"] or "[]")
            return {"id": action_id, "signs_count": len(signs), "sign": sign}

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Archive a creation action ────────────────────────────────────────────────

@router.delete("/api/creation/actions/{action_id}")
async def archive_creation_action(action_id: int):
    """Soft-delete a creation action (archive it)."""
    from src.memory.database import get_db

    try:
        async with get_db() as conn:
            result = await conn.execute(
                """UPDATE creation_actions
                   SET archived = TRUE, updated_at = NOW()
                   WHERE id = %s AND archived = FALSE
                   RETURNING id""",
                (action_id,),
            )
            row = await result.fetchone()
            if not row:
                return JSONResponse({"error": "not found"}, status_code=404)

            return {"id": action_id, "archived": True}

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
