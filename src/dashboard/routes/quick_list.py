"""
Quick Lists — lightweight list management for daily use.

Each list is a card on the home board (groceries, ideas, errands, etc).
Items within a list are checkable. Lists can be pinned to show on the
home board or accessed from the full list view.

In multi-Presence mode (COVE_MODE=multi), lists are scoped per Presence
via the presence_id column. In single mode, presence_id is NULL.

Soft-delete: lists and items are archived (never hard deleted) so users
can review past data. Activity log tracks all changes for version history.

API:
  GET    /api/quick-lists              — all active lists (with item counts)
  POST   /api/quick-lists              — create a list
  PATCH  /api/quick-lists/{id}         — update list name/icon/position/pinned
  DELETE /api/quick-lists/{id}         — archive a list (soft delete)
  POST   /api/quick-lists/{id}/restore — restore an archived list
  GET    /api/quick-lists/archived     — archived lists
  GET    /api/quick-lists/{id}/items   — items in a list
  POST   /api/quick-lists/{id}/items   — add item(s) to a list
  PATCH  /api/quick-lists/items/{id}   — update item (text, checked, position)
  DELETE /api/quick-lists/items/{id}   — archive an item (soft delete)
  POST   /api/quick-lists/{id}/clear   — archive checked items from a list
  GET    /api/quick-lists/{id}/activity — activity history for a list
"""

import os
from src.env import env
from datetime import datetime, timezone

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse

router = APIRouter()

COVE_MODE = env("COVE_MODE", "single")


async def _get_presence_id(request: Request):
    """Get presence_id from cookie in multi mode. Returns None in single mode."""
    if COVE_MODE != "multi":
        return None
    try:
        from src.dashboard.routes.presence import get_current_presence
        presence = await get_current_presence(request)
        return presence["id"] if presence else None
    except Exception:
        return None


def _presence_filter(presence_id):
    """Return SQL WHERE clause fragment and params for presence scoping."""
    if presence_id:
        return "presence_id = %s", (presence_id,)
    return "presence_id IS NULL", ()


async def _log_activity(conn, list_id, action, presence_id=None, item_id=None, detail=None):
    """Log an action to quick_list_activity for version history."""
    try:
        await conn.execute(
            """INSERT INTO quick_list_activity (list_id, item_id, presence_id, action, detail)
               VALUES (%s, %s, %s, %s, %s)""",
            (list_id, item_id, presence_id, action, detail),
        )
    except Exception:
        pass  # Activity logging is best-effort, never block the operation


# =============================================================================
# Lists
# =============================================================================

@router.get("/api/quick-lists")
async def get_lists(request: Request):
    """Get all quick lists with item counts."""
    from src.memory.database import get_db

    presence_id = await _get_presence_id(request)
    where, params = _presence_filter(presence_id)

    async with get_db() as conn:
        result = await conn.execute(
            f"""SELECT ql.id, ql.name, ql.icon, ql.color, ql.position, ql.pinned,
                       ql.created_at, ql.updated_at,
                       COUNT(qli.id) FILTER (WHERE qli.checked = FALSE AND (qli.archived IS NULL OR qli.archived = FALSE)) AS unchecked,
                       COUNT(qli.id) FILTER (WHERE qli.archived IS NULL OR qli.archived = FALSE) AS total
                FROM quick_lists ql
                LEFT JOIN quick_list_items qli ON qli.list_id = ql.id
                WHERE {where} AND (ql.archived IS NULL OR ql.archived = FALSE)
                GROUP BY ql.id
                ORDER BY ql.position, ql.created_at""",
            params
        )
        rows = await result.fetchall()

    lists = []
    for r in rows:
        lists.append({
            "id": r["id"],
            "name": r["name"],
            "icon": r["icon"],
            "color": r["color"],
            "position": r["position"],
            "pinned": r["pinned"],
            "unchecked": r["unchecked"],
            "total": r["total"],
        })

    return {"lists": lists}


@router.post("/api/quick-lists")
async def create_list(request: Request):
    """Create a new quick list."""
    from src.memory.database import get_db

    body = await request.json()
    name = body.get("name", "").strip()
    if not name:
        raise HTTPException(400, "name is required")

    icon = body.get("icon", "📋")
    color = body.get("color")
    pinned = body.get("pinned", True)
    presence_id = await _get_presence_id(request)

    async with get_db() as conn:
        # Get next position
        where, params = _presence_filter(presence_id)
        result = await conn.execute(
            f"SELECT COALESCE(MAX(position), -1) + 1 AS next_pos FROM quick_lists WHERE {where}",
            params
        )
        row = await result.fetchone()
        position = row["next_pos"]

        result = await conn.execute(
            """INSERT INTO quick_lists (presence_id, name, icon, color, position, pinned)
               VALUES (%s, %s, %s, %s, %s, %s)
               RETURNING id, name, icon, color, position, pinned, created_at""",
            (presence_id, name, icon, color, position, pinned)
        )
        created = await result.fetchone()

    return {
        "id": created["id"],
        "name": created["name"],
        "icon": created["icon"],
        "color": created["color"],
        "position": created["position"],
        "pinned": created["pinned"],
        "unchecked": 0,
        "total": 0,
    }


@router.patch("/api/quick-lists/{list_id}")
async def update_list(list_id: int, request: Request):
    """Update a quick list's name, icon, color, position, or pinned state."""
    from src.memory.database import get_db

    body = await request.json()
    updates = []
    params = []

    for field in ("name", "icon", "color"):
        if field in body:
            updates.append(f"{field} = %s")
            params.append(body[field])
    for field in ("position",):
        if field in body:
            updates.append(f"{field} = %s")
            params.append(int(body[field]))
    if "pinned" in body:
        updates.append("pinned = %s")
        params.append(bool(body["pinned"]))

    if not updates:
        raise HTTPException(400, "Nothing to update")

    presence_id = await _get_presence_id(request)

    updates.append("updated_at = NOW()")
    params.append(list_id)

    async with get_db() as conn:
        if presence_id:
            # Ownership gate: only update if this list belongs to the caller.
            owned = await conn.execute(
                "SELECT id FROM quick_lists WHERE id = %s AND presence_id = %s",
                (list_id, presence_id),
            )
            if not await owned.fetchone():
                return JSONResponse({"error": "not found"}, status_code=404)
        await conn.execute(
            f"UPDATE quick_lists SET {', '.join(updates)} WHERE id = %s",
            tuple(params)
        )

    return {"ok": True}


@router.delete("/api/quick-lists/{list_id}")
async def delete_list(list_id: int, request: Request):
    """Archive a list (soft delete). Items are preserved."""
    from src.memory.database import get_db

    presence_id = await _get_presence_id(request)

    async with get_db() as conn:
        if presence_id:
            owned = await conn.execute(
                "SELECT id FROM quick_lists WHERE id = %s AND presence_id = %s",
                (list_id, presence_id),
            )
            if not await owned.fetchone():
                return JSONResponse({"error": "not found"}, status_code=404)
        await conn.execute(
            "UPDATE quick_lists SET archived = TRUE, archived_at = NOW() WHERE id = %s",
            (list_id,),
        )
        await _log_activity(conn, list_id, 'list_archived', presence_id)

    return {"ok": True}


@router.post("/api/quick-lists/{list_id}/restore")
async def restore_list(list_id: int, request: Request):
    """Restore an archived list."""
    from src.memory.database import get_db

    presence_id = await _get_presence_id(request)

    async with get_db() as conn:
        if presence_id:
            owned = await conn.execute(
                "SELECT id FROM quick_lists WHERE id = %s AND presence_id = %s",
                (list_id, presence_id),
            )
            if not await owned.fetchone():
                return JSONResponse({"error": "not found"}, status_code=404)
        await conn.execute(
            "UPDATE quick_lists SET archived = FALSE, archived_at = NULL WHERE id = %s",
            (list_id,),
        )
        # Also restore any archived items in this list
        await conn.execute(
            "UPDATE quick_list_items SET archived = FALSE, archived_at = NULL WHERE list_id = %s AND archived = TRUE",
            (list_id,),
        )
        await _log_activity(conn, list_id, 'list_restored', presence_id)

    return {"ok": True}


@router.get("/api/quick-lists/archived")
async def get_archived_lists(request: Request):
    """Get archived lists for review."""
    from src.memory.database import get_db

    presence_id = await _get_presence_id(request)
    where, params = _presence_filter(presence_id)

    async with get_db() as conn:
        result = await conn.execute(
            f"""SELECT ql.id, ql.name, ql.icon, ql.color, ql.archived_at,
                       COUNT(qli.id) AS total
                FROM quick_lists ql
                LEFT JOIN quick_list_items qli ON qli.list_id = ql.id
                WHERE {where} AND ql.archived = TRUE
                GROUP BY ql.id
                ORDER BY ql.archived_at DESC""",
            params
        )
        rows = await result.fetchall()

    lists = []
    for r in rows:
        lists.append({
            "id": r["id"],
            "name": r["name"],
            "icon": r["icon"],
            "color": r["color"],
            "archived_at": r["archived_at"].isoformat() if r["archived_at"] else None,
            "total": r["total"],
        })

    return {"lists": lists}


# =============================================================================
# Items
# =============================================================================

@router.get("/api/quick-lists/{list_id}/items")
async def get_items(list_id: int, request: Request):
    """Get all items in a list."""
    from src.memory.database import get_db

    presence_id = await _get_presence_id(request)

    async with get_db() as conn:
        if presence_id:
            owned = await conn.execute(
                "SELECT id FROM quick_lists WHERE id = %s AND presence_id = %s",
                (list_id, presence_id),
            )
            if not await owned.fetchone():
                return JSONResponse({"error": "not found"}, status_code=404)
        result = await conn.execute(
            """SELECT id, text, checked, position, created_at, checked_at
               FROM quick_list_items
               WHERE list_id = %s AND (archived IS NULL OR archived = FALSE)
               ORDER BY checked ASC, position ASC, created_at ASC""",
            (list_id,)
        )
        rows = await result.fetchall()

    items = []
    for r in rows:
        items.append({
            "id": r["id"],
            "text": r["text"],
            "checked": r["checked"],
            "position": r["position"],
        })

    return {"list_id": list_id, "items": items}


@router.post("/api/quick-lists/{list_id}/items")
async def add_items(list_id: int, request: Request):
    """Add one or more items to a list.

    Body: { "text": "Milk" }
    or:   { "items": ["Milk", "Eggs", "Bread"] }
    """
    from src.memory.database import get_db

    body = await request.json()

    # Support single or batch
    texts = []
    if "items" in body:
        texts = [t.strip() for t in body["items"] if t.strip()]
    elif "text" in body:
        t = body["text"].strip()
        if t:
            texts = [t]

    if not texts:
        raise HTTPException(400, "text or items required")

    presence_id = await _get_presence_id(request)

    async with get_db() as conn:
        if presence_id:
            owned = await conn.execute(
                "SELECT id FROM quick_lists WHERE id = %s AND presence_id = %s",
                (list_id, presence_id),
            )
            if not await owned.fetchone():
                return JSONResponse({"error": "not found"}, status_code=404)
        # Get next position
        result = await conn.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 AS next_pos FROM quick_list_items WHERE list_id = %s",
            (list_id,)
        )
        row = await result.fetchone()
        pos = row["next_pos"]

        added = []
        for text in texts:
            result = await conn.execute(
                """INSERT INTO quick_list_items (list_id, text, position)
                   VALUES (%s, %s, %s)
                   RETURNING id, text, checked, position""",
                (list_id, text, pos)
            )
            item = await result.fetchone()
            added.append({
                "id": item["id"],
                "text": item["text"],
                "checked": item["checked"],
                "position": item["position"],
            })
            await _log_activity(conn, list_id, 'item_added', presence_id, item["id"], text)
            pos += 1

    return {"items": added}


@router.patch("/api/quick-lists/items/{item_id}")
async def update_item(item_id: int, request: Request):
    """Update an item's text, checked state, or position."""
    from src.memory.database import get_db

    body = await request.json()
    presence_id = await _get_presence_id(request)
    updates = []
    params = []

    # Track what changed for activity logging
    text_changed = "text" in body
    check_changed = "checked" in body

    if text_changed:
        updates.append("text = %s")
        params.append(body["text"].strip())
    if check_changed:
        updates.append("checked = %s")
        params.append(bool(body["checked"]))
        if body["checked"]:
            updates.append("checked_at = NOW()")
        else:
            updates.append("checked_at = NULL")
    if "position" in body:
        updates.append("position = %s")
        params.append(int(body["position"]))

    if not updates:
        raise HTTPException(400, "Nothing to update")

    params.append(item_id)

    async with get_db() as conn:
        # Get current state for activity logging — scoped to the caller's lists
        # in multi mode so you can't touch another presence's item.
        if presence_id:
            old_result = await conn.execute(
                """SELECT list_id, text FROM quick_list_items
                   WHERE id = %s
                     AND list_id IN (SELECT id FROM quick_lists WHERE presence_id = %s)""",
                (item_id, presence_id),
            )
        else:
            old_result = await conn.execute(
                "SELECT list_id, text FROM quick_list_items WHERE id = %s", (item_id,)
            )
        old_row = await old_result.fetchone()
        if presence_id and not old_row:
            return JSONResponse({"error": "not found"}, status_code=404)
        list_id = old_row["list_id"] if old_row else None

        if presence_id:
            await conn.execute(
                f"""UPDATE quick_list_items SET {', '.join(updates)}
                    WHERE id = %s
                      AND list_id IN (SELECT id FROM quick_lists WHERE presence_id = %s)""",
                tuple(params) + (presence_id,)
            )
        else:
            await conn.execute(
                f"UPDATE quick_list_items SET {', '.join(updates)} WHERE id = %s",
                tuple(params)
            )

        # Log activity
        if list_id:
            if check_changed:
                action = 'item_checked' if body["checked"] else 'item_unchecked'
                await _log_activity(conn, list_id, action, presence_id, item_id)
            if text_changed and old_row:
                await _log_activity(conn, list_id, 'item_edited', presence_id, item_id,
                                    old_row["text"])

    return {"ok": True}


@router.delete("/api/quick-lists/items/{item_id}")
async def delete_item(item_id: int, request: Request):
    """Archive a single item (soft delete)."""
    from src.memory.database import get_db

    presence_id = await _get_presence_id(request)

    async with get_db() as conn:
        if presence_id:
            result = await conn.execute(
                """SELECT list_id FROM quick_list_items
                   WHERE id = %s
                     AND list_id IN (SELECT id FROM quick_lists WHERE presence_id = %s)""",
                (item_id, presence_id),
            )
        else:
            result = await conn.execute(
                "SELECT list_id FROM quick_list_items WHERE id = %s", (item_id,)
            )
        row = await result.fetchone()
        if presence_id and not row:
            return JSONResponse({"error": "not found"}, status_code=404)

        if presence_id:
            await conn.execute(
                """UPDATE quick_list_items SET archived = TRUE, archived_at = NOW()
                   WHERE id = %s
                     AND list_id IN (SELECT id FROM quick_lists WHERE presence_id = %s)""",
                (item_id, presence_id),
            )
        else:
            await conn.execute(
                "UPDATE quick_list_items SET archived = TRUE, archived_at = NOW() WHERE id = %s",
                (item_id,),
            )

        if row:
            await _log_activity(conn, row["list_id"], 'item_archived', presence_id, item_id)

    return {"ok": True}


@router.post("/api/quick-lists/{list_id}/clear")
async def clear_checked(list_id: int, request: Request):
    """Archive all checked items from a list (soft delete)."""
    from src.memory.database import get_db

    presence_id = await _get_presence_id(request)

    async with get_db() as conn:
        if presence_id:
            owned = await conn.execute(
                "SELECT id FROM quick_lists WHERE id = %s AND presence_id = %s",
                (list_id, presence_id),
            )
            if not await owned.fetchone():
                return JSONResponse({"error": "not found"}, status_code=404)
        result = await conn.execute(
            """UPDATE quick_list_items
               SET archived = TRUE, archived_at = NOW()
               WHERE list_id = %s AND checked = TRUE AND (archived IS NULL OR archived = FALSE)""",
            (list_id,)
        )
        cleared = result.rowcount if hasattr(result, 'rowcount') else 0

        if cleared > 0:
            await _log_activity(conn, list_id, 'checked_archived', presence_id,
                                detail=f'{cleared} items')

    return {"ok": True, "cleared": cleared}


@router.get("/api/quick-lists/{list_id}/activity")
async def get_list_activity(list_id: int, request: Request):
    """Activity history for a list — version tracking."""
    from src.memory.database import get_db

    presence_id = await _get_presence_id(request)

    async with get_db() as conn:
        if presence_id:
            owned = await conn.execute(
                "SELECT id FROM quick_lists WHERE id = %s AND presence_id = %s",
                (list_id, presence_id),
            )
            if not await owned.fetchone():
                return JSONResponse({"error": "not found"}, status_code=404)
        result = await conn.execute(
            """SELECT id, list_id, item_id, action, detail, created_at
               FROM quick_list_activity
               WHERE list_id = %s
               ORDER BY created_at DESC
               LIMIT 200""",
            (list_id,)
        )
        rows = await result.fetchall()

    activities = []
    for r in rows:
        activities.append({
            "id": r["id"],
            "item_id": r["item_id"],
            "action": r["action"],
            "detail": r["detail"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        })

    return {"activity": activities}
