"""
Contact messages — user feedback and questions from Help.

Endpoints:
  - POST /api/contact          — Submit (session if present)
  - POST /api/contact/submit   — Same handler (public path in multi mode)
  - GET  /api/contact/messages — List messages (secret for Haven MC)
  - PATCH /api/contact/messages/{id}/archive — Archive a message
"""

import hmac
import re

from fastapi import APIRouter, Request, HTTPException

from src.env import env

router = APIRouter()

COVE_MODE = env("COVE_MODE", "single")
CONTACT_SECRET = env("SHARED_CONTAINER_SECRET")
FORWARD_HEADER = "X-Contact-Forward"


def shared_hub_url():
    return (env("SHARED_CONTAINER_URL") or "").rstrip("/")


def should_proxy_to_hub(request: Request) -> bool:
    if (request.headers.get(FORWARD_HEADER) or "") == "1":
        return False
    return bool(shared_hub_url())


async def proxy_to_hub(request: Request, method: str, path: str, json_body=None):
    """Cove/founder Help → one inbox on the shared container. None = handle locally."""
    if not should_proxy_to_hub(request):
        return None
    import httpx

    dest = shared_hub_url() + path
    query = str(request.url.query or "")
    if query:
        dest = dest + "?" + query
    headers = {FORWARD_HEADER: "1", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            res = await client.request(method, dest, json=json_body, headers=headers)
    except httpx.RequestError as exc:
        raise HTTPException(502, f"Shared inbox unreachable ({type(exc).__name__})")
    try:
        data = res.json()
    except Exception:
        data = {}
    if res.is_success and isinstance(data, dict):
        return data
    detail = data.get("detail") if isinstance(data, dict) else None
    if not isinstance(detail, str) or not detail:
        detail = "Shared inbox error"
    raise HTTPException(min(max(res.status_code, 400), 502), detail)

_PRODUCTS = {
    "lucid-tuner": "Lucid Tuner",
    "lucid-cove-hermes": "Lucid Cove on Hermes",
    "lucid-cove": "Lucid Cove",
}
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _clean(value, limit=200):
    text = str(value or "").replace("\r", " ").replace("\n", " ").replace("\0", "")
    return text.strip()[:limit]


def normalize_product(raw):
    key = _clean(raw, 64).lower().replace(" ", "-")
    if key in _PRODUCTS:
        return _PRODUCTS[key]
    lowered = _clean(raw, 64).lower()
    for label in _PRODUCTS.values():
        if lowered == label.lower():
            return label
    return ""


def compose_contact_fields(*, message, subject="", product="", host="", path="",
                           handle="", email="", name="", tier="", connected=""):
    """Build subject + labeled body so Haven MC can see where it came from."""
    user_message = _clean(message, 5000)
    product = normalize_product(product) or "Help"
    host = _clean(host, 120).split(" ")[0]
    path = _clean(path, 120)
    handle = _clean(handle, 64)
    if handle and not handle.startswith("@"):
        handle = "@" + handle
    email = _clean(email, 200)
    name = _clean(name, 120)
    tier = _clean(tier, 40)
    connected = _clean(connected, 20)
    subject = _clean(subject, 200)
    if not subject:
        subject = " · ".join(part for part in (product, host, handle) if part)[:200]
    lines = []
    for label, value in (
        ("product", product),
        ("host", host),
        ("path", path),
        ("handle", handle),
        ("name", name),
        ("email", email),
        ("tier", tier),
        ("connected", connected),
    ):
        if value:
            lines.append(f"{label}: {value}")
    labeled = "\n".join(lines)
    body = f"{labeled}\n\n{user_message}" if labeled else user_message
    return subject, body


async def _submit_contact(request: Request):
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "Invalid JSON")
    message = (body.get("message") or "").strip()
    if not message:
        raise HTTPException(400, "Message is required")
    if len(message) > 5000:
        raise HTTPException(400, "Message too long (max 5000 characters)")

    account_id = None
    email = ""
    display_name = ""
    username = ""
    tier = "free"

    try:
        from src.dashboard.routes.presence import get_current_presence
        presence = await get_current_presence(request)
        if presence:
            account_id = presence.get("id")
            email = presence.get("email", "") or ""
            display_name = presence.get("display_name", "") or ""
            username = presence.get("username", "") or ""
            tier = presence.get("tier", "free") or presence.get("cove_role") or ""
    except Exception:
        pass

    if not email:
        email = _clean(body.get("email"), 200)
    if email and not _EMAIL_RE.match(email):
        email = ""
    if not display_name:
        display_name = _clean(body.get("name") or body.get("display_name"), 120)
    if not username:
        username = _clean(body.get("handle") or body.get("username"), 64).lstrip("@")

    host = _clean(body.get("host"), 120) or _clean(request.headers.get("host"), 120)
    product = body.get("product") or body.get("source") or ""
    connected = body.get("connected")
    if connected is True:
        connected = "yes"
    elif connected is False:
        connected = "no"
    subject, labeled = compose_contact_fields(
        message=message,
        subject=body.get("subject") or "",
        product=product,
        host=host,
        path=body.get("path") or "",
        handle=username,
        email=email,
        name=display_name,
        tier=tier,
        connected=connected if connected in ("yes", "no") else _clean(connected, 20),
    )

    hub_payload = {
        "message": message,
        "email": email,
        "name": display_name,
        "handle": username,
        "product": product or "lucid-cove",
        "host": host,
        "path": body.get("path") or "",
        "connected": connected if connected in ("yes", "no") else _clean(connected, 20),
        "subject": subject,
    }
    proxied = await proxy_to_hub(request, "POST", "/api/contact/submit", hub_payload)
    if proxied is not None:
        return proxied

    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            await conn.execute(
                """INSERT INTO contact_messages
                   (account_id, email, display_name, username, tier, subject, message)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (account_id, email, display_name, username, tier, subject, labeled),
            )
    except Exception as e:
        raise HTTPException(500, f"Database error: {e}")

    return {"ok": True, "message": "Message sent. Thank you for your feedback."}


@router.post("/api/contact")
async def submit_contact(request: Request):
    """Submit a feedback/question message from Help."""
    return await _submit_contact(request)


@router.post("/api/contact/submit")
async def submit_contact_public(request: Request):
    """Public alias so Tuner / house Help can send without a Cove session."""
    return await _submit_contact(request)


@router.get("/api/contact/messages")
async def list_messages(request: Request):
    """List contact messages. Protected by shared secret (for Haven MC)."""
    secret = request.query_params.get("secret", "")
    if not CONTACT_SECRET or not hmac.compare_digest(secret, CONTACT_SECRET):
        raise HTTPException(403, "Unauthorized")

    proxied = await proxy_to_hub(request, "GET", "/api/contact/messages")
    if proxied is not None:
        return proxied

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
                (show_archived, limit),
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
    if not isinstance(body, dict):
        raise HTTPException(400, "Invalid JSON")
    archive = body.get("archived", True)

    proxied = await proxy_to_hub(
        request, "PATCH", f"/api/contact/messages/{message_id}/archive", body
    )
    if proxied is not None:
        return proxied

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
                (archive, message_id),
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Database error: {e}")

    return {"ok": True, "id": message_id, "archived": archive}
