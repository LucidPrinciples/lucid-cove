"""
Contact messages — user feedback and questions from the help overlay.

Endpoints:
  - POST /api/contact          — Submit a message (authenticated users)
  - GET  /api/contact/messages — List messages (protected by secret for Haven MC)
  - PATCH /api/contact/messages/{id}/archive — Archive a message
"""

import hmac
import os
from src.env import env
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request, HTTPException

router = APIRouter()

COVE_MODE = env("COVE_MODE", "single")
CONTACT_SECRET = env("SHARED_CONTAINER_SECRET")


@router.post("/api/contact")
async def submit_contact(request: Request):
    """Submit a feedback/question message from the help overlay."""
    body = await request.json()
    message = (body.get("message") or "").strip()
    subject = (body.get("subject") or "").strip()

    if not message:
        raise HTTPException(400, "Message is required")
    if len(message) > 5000:
        raise HTTPException(400, "Message too long (max 5000 characters)")

    # Get current user info if authenticated
    account_id = None
    email = ""
    display_name = ""
    username = ""
    tier = "free"

    if COVE_MODE == "multi":
        try:
            from src.dashboard.routes.presence import get_current_presence
            presence = await get_current_presence(request)
            if presence:
                account_id = presence.get("id")
                email = presence.get("email", "")
                display_name = presence.get("display_name", "")
                username = presence.get("username", "")
                tier = presence.get("tier", "free")
        except Exception:
            pass

    # Allow anonymous email if not authenticated
    if not email:
        email = (body.get("email") or "").strip()
        if not email:
            raise HTTPException(400, "Email is required")

    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            await conn.execute(
                """INSERT INTO contact_messages
                   (account_id, email, display_name, username, tier, subject, message)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (account_id, email, display_name, username, tier, subject, message)
            )
    except Exception as e:
        raise HTTPException(500, f"Database error: {e}")

    return {"ok": True, "message": "Message sent. Thank you for your feedback."}


@router.get("/api/contact/messages")
async def list_messages(request: Request):
    """List contact messages. Protected by shared secret (for Haven MC)."""
    secret = request.query_params.get("secret", "")
    if not CONTACT_SECRET or not hmac.compare_digest(secret, CONTACT_SECRET):
        raise HTTPException(403, "Unauthorized")

    show_archived = request.query_params.get("archived", "false") == "true"
    limit = min(int(request.query_params.get("limit", "50")), 200)

    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            result = await conn.execute(
                """SELECT id, account_id, email, display_name, username, tier,
                          subject, message, archived, archived_at, created_at
                   FROM contact_messages
                   WHERE archived = %s
                   ORDER BY created_at DESC
                   LIMIT %s""",
                (show_archived, limit)
            )
            rows = await result.fetchall()
    except Exception as e:
        raise HTTPException(500, f"Database error: {e}")

    return {
        "messages": [
            {
                "id": r["id"],
                "email": r["email"],
                "display_name": r["display_name"],
                "username": r["username"],
                "tier": r["tier"],
                "subject": r["subject"],
                "message": r["message"],
                "archived": r["archived"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in rows
        ],
        "count": len(rows),
    }


@router.patch("/api/contact/messages/{message_id}/archive")
async def archive_message(message_id: int, request: Request):
    """Archive (or unarchive) a contact message."""
    secret = request.query_params.get("secret", "")
    if not CONTACT_SECRET or not hmac.compare_digest(secret, CONTACT_SECRET):
        raise HTTPException(403, "Unauthorized")

    body = await request.json()
    archive = body.get("archived", True)

    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            result = await conn.execute(
                "SELECT id FROM contact_messages WHERE id = %s", (message_id,)
            )
            if not await result.fetchone():
                raise HTTPException(404, "Message not found")

            archived_at = "NOW()" if archive else "NULL"
            await conn.execute(
                f"""UPDATE contact_messages
                    SET archived = %s, archived_at = {archived_at}
                    WHERE id = %s""",
                (archive, message_id)
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Database error: {e}")

    return {"ok": True, "id": message_id, "archived": archive}
