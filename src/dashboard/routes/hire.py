"""
hire.py — Hire layer endpoints (#169).

User-facing endpoints (session-scoped) for the logged-in Presence to hire a
seller, and for a seller to work the engagement: request → accept → deliver →
release (or cancel → refund). The money + records live on the hub (with the
ledger), so a Cove proxies to the hub; the hub runs it in-process.

  User (session)            Service (secret, on the hub)
  POST /api/hire/request -> POST /api/hire-svc/open
  POST /api/hire/act     -> POST /api/hire-svc/act
  GET  /api/hire/inbox   -> GET  /api/hire-svc/list
"""
import hmac
import os
from src.env import env, env_bool

from fastapi import APIRouter, Request, HTTPException

router = APIRouter()

SECRET = env("SHARED_CONTAINER_SECRET")


def _is_master() -> bool:
    return env_bool("LP_REGISTRY_MASTER")


def _require_secret(request: Request, body: dict = None):
    supplied = request.headers.get("X-Shared-Secret", "") or ((body or {}).get("secret") or "")
    if not SECRET:
        raise HTTPException(501, "Hire not configured (SHARED_CONTAINER_SECRET unset)")
    if not (supplied and hmac.compare_digest(supplied, SECRET)):
        raise HTTPException(403, "Invalid secret")


async def _current_handle(request: Request) -> str:
    from src.dashboard.routes.presence import get_current_presence
    presence = await get_current_presence(request)
    if not presence:
        raise HTTPException(401, "Sign in first")
    return (presence.get("username") or "").lstrip("@").lower()


def _http_status_for(exc: Exception) -> int:
    if isinstance(exc, PermissionError):
        return 403
    # "insufficient" / "negative" → Payment Required; other ValueErrors → 400
    msg = str(exc).lower()
    return 402 if ("insufficient" in msg or "negative" in msg or "short" in msg) else 400


# ── In-process (hub) execution of a hire op ──────────────────────────────────
async def _run(op: str, payload: dict):
    from src.economy import hire
    if op == "open":
        return await hire.open_hire(
            buyer_handle=payload["buyer_handle"], seller_handle=payload.get("seller_handle"),
            amount_credits=payload.get("amount_credits"), title=payload.get("title", ""),
            listing_ref=payload.get("listing_ref"))
    if op == "act":
        return await hire.act_on_hire(
            hire_id=int(payload["hire_id"]), actor_handle=payload["actor_handle"],
            action=payload.get("action"), delivery_ref=payload.get("delivery_ref"))
    if op == "list":
        return {"hires": await hire.list_hires(
            handle=payload["handle"], role=payload.get("role", "buyer"))}
    raise HTTPException(400, f"unknown hire op '{op}'")


# ── Route a hire op: in-process on the hub, else proxy to the hub ─────────────
async def _dispatch(op: str, payload: dict, method: str = "POST"):
    if _is_master():
        try:
            return await _run(op, payload)
        except (ValueError, PermissionError) as e:
            raise HTTPException(_http_status_for(e), str(e))
    base = env("LP_REGISTRY_URL").rstrip("/")
    if not (base and SECRET):
        raise HTTPException(501, "Hire hub not reachable (LP_REGISTRY_URL/secret unset)")
    import httpx
    url = f"{base}/api/hire-svc/{op}"
    headers = {"X-Shared-Secret": SECRET}
    async with httpx.AsyncClient(timeout=20) as client:
        if method == "GET":
            r = await client.get(url, headers=headers, params=payload)
        else:
            r = await client.post(url, headers=headers, json={**payload, "secret": SECRET})
    if r.status_code >= 400:
        raise HTTPException(r.status_code, (r.json() or {}).get("detail", "Hire failed"))
    return r.json()


# ── User-facing (session) ────────────────────────────────────────────────────
@router.post("/api/hire/request")
async def hire_request(request: Request):
    """Start a Hire. Body: { seller_handle, amount_credits, title?, listing_ref? }."""
    body = await request.json()
    buyer = await _current_handle(request)
    return await _dispatch("open", {
        "buyer_handle": buyer, "seller_handle": body.get("seller_handle"),
        "amount_credits": body.get("amount_credits"), "title": body.get("title", ""),
        "listing_ref": body.get("listing_ref")})


@router.post("/api/hire/act")
async def hire_act(request: Request):
    """Advance a Hire. Body: { hire_id, action: accept|deliver|release|cancel, delivery_ref? }."""
    body = await request.json()
    actor = await _current_handle(request)
    return await _dispatch("act", {
        "hire_id": body.get("hire_id"), "actor_handle": actor,
        "action": body.get("action"), "delivery_ref": body.get("delivery_ref")})


@router.get("/api/hire/inbox")
async def hire_inbox(request: Request, role: str = "buyer"):
    """The current Presence's hires — role=buyer (orders) or role=seller (Work inbox)."""
    handle = await _current_handle(request)
    return await _dispatch("list", {"handle": handle, "role": role}, method="GET")


# ── Service (secret-gated, run on the hub) ───────────────────────────────────
@router.post("/api/hire-svc/open")
async def svc_open(request: Request):
    body = await request.json()
    _require_secret(request, body)
    try:
        return await _run("open", body)
    except (ValueError, PermissionError) as e:
        raise HTTPException(_http_status_for(e), str(e))


@router.post("/api/hire-svc/act")
async def svc_act(request: Request):
    body = await request.json()
    _require_secret(request, body)
    try:
        return await _run("act", body)
    except (ValueError, PermissionError) as e:
        raise HTTPException(_http_status_for(e), str(e))


@router.get("/api/hire-svc/list")
async def svc_list(request: Request, handle: str = "", role: str = "buyer"):
    _require_secret(request)
    return await _run("list", {"handle": handle, "role": role})
