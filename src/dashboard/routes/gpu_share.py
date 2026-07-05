"""
GPU-share grants — the provider side of cross-Cove GPU sharing.

A Cove admin with a GPU mints a credential (raw token, shown once + this Cove's public GPU
endpoint) and hands it to a friend out-of-band. The friend's Cove pastes it, which routes
its video transcription to this Cove's GPU (compute.video_asr = external + token). This
Cove's pipecat then requires that token, so the GPU isn't open to the world; the provider
can revoke any grant at any time.

Only the token HASH is stored. See LP-Vault/Reference/cross-cove-gpu-share-spec.md.
Admin-Presence gated, like the other compute routes. /api/gpu/verify is the internal
endpoint pipecat calls to validate an incoming grant token.
"""

import hashlib
import hmac
import logging
import secrets
from typing import Optional

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.env import env, env_bool

logger = logging.getLogger(__name__)

router = APIRouter()


def _hash_token(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


async def _is_admin(request: Request) -> bool:
    """Admin-Presence only (same gate the compute routes use)."""
    try:
        from src.dashboard.routes.settings import _is_admin_presence
        return await _is_admin_presence(request)
    except Exception:
        return False


def _public_gpu_endpoint() -> str:
    """This Cove's public GPU/voice URL — what a renter points compute.video_asr.url at.
    Explicit VOICE_PUBLIC_URL wins; else derive https://voice.<cove-domain> from cove.yaml."""
    explicit = (env("VOICE_PUBLIC_URL") or "").strip()
    if explicit:
        return explicit.rstrip("/")
    try:
        from src.config import load_cove_config
        dom = (load_cove_config().get("domain") or "").strip()
        if dom:
            return f"https://voice.{dom}"
    except Exception:
        pass
    return ""


class GrantCreate(BaseModel):
    label: Optional[str] = ""


class TokenBody(BaseModel):
    token: str


@router.post("/api/gpu/grants")
async def mint_grant(body: GrantCreate, request: Request):
    """Mint a GPU-share credential. Returns the raw token ONCE + this Cove's endpoint."""
    if not await _is_admin(request):
        return JSONResponse(status_code=403, content={"error": "Admin Presence only"})
    token = "gpugrant_" + secrets.token_hex(24)
    label = (body.label or "").strip()[:120]
    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            result = await conn.execute(
                "INSERT INTO gpu_grants (token_hash, label) VALUES (%s, %s) RETURNING id, created_at",
                (_hash_token(token), label),
            )
            row = await result.fetchone()
    except Exception as e:
        logger.error(f"mint_grant failed: {e}")
        return JSONResponse(status_code=500, content={"error": f"Could not mint grant: {e}"})
    return {
        "ok": True,
        "id": row["id"],
        "label": label,
        "token": token,                       # shown ONCE — not retrievable later
        "endpoint": _public_gpu_endpoint(),
        "created_at": row["created_at"],
    }


@router.get("/api/gpu/grants")
async def list_grants(request: Request):
    """List this Cove's GPU-share grants (no raw tokens)."""
    if not await _is_admin(request):
        return JSONResponse(status_code=403, content={"error": "Admin Presence only"})
    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            result = await conn.execute(
                "SELECT id, label, created_at, last_used_at, revoked "
                "FROM gpu_grants ORDER BY created_at DESC"
            )
            rows = await result.fetchall()
    except Exception as e:
        logger.warning(f"list_grants (table missing?): {e}")
        return {"grants": [], "endpoint": _public_gpu_endpoint()}
    return {"grants": list(rows), "endpoint": _public_gpu_endpoint()}


@router.post("/api/gpu/grants/{grant_id}/revoke")
async def revoke_grant(grant_id: int, request: Request):
    """Revoke a grant — the GPU on/off control for that renter."""
    if not await _is_admin(request):
        return JSONResponse(status_code=403, content={"error": "Admin Presence only"})
    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            result = await conn.execute(
                "UPDATE gpu_grants SET revoked = TRUE WHERE id = %s RETURNING id",
                (grant_id,),
            )
            row = await result.fetchone()
    except Exception as e:
        logger.error(f"revoke_grant failed: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})
    if not row:
        return JSONResponse(status_code=404, content={"error": "grant not found"})
    return {"ok": True, "id": grant_id, "revoked": True}


# ── Marketplace offer (CF-87) — list this GPU on the Market ──────────────────
# Thin admin-gated proxies to Socrates' seller-token path (/api/marketplace/gpu/*).
# The operator token authenticates the seller (market._socrates sends it); the
# marketplace resolves it to OUR handle server-side, so a Cove can only ever list
# its own GPU. Purchases do NOT auto-open the GPU — access stays behind the grant
# tokens above (mint per renter, revoke any time).

class OfferPublish(BaseModel):
    title: Optional[str] = ""
    description: Optional[str] = ""
    price_cents: int = 0                     # USD cents; fixed-price rail, $12 min (or 0 = free)
    billing_note: Optional[str] = ""         # e.g. "$1/month, billed yearly"
    unit: Optional[str] = "per job"


@router.post("/api/gpu/offer/publish")
async def publish_offer(body: OfferPublish, request: Request):
    """List (or update) this Cove's GPU on the marketplace (fixed-price rail). Admin only.
    Launch rail (marketplace-v1): a priced offer is a fixed-price USD tier at the $12 floor
    (the hub enforces the floor); 0 = free/trusted-Cove."""
    if not await _is_admin(request):
        return JSONResponse(status_code=403, content={"error": "Admin Presence only"})
    endpoint = _public_gpu_endpoint()
    if not endpoint:
        return JSONResponse(status_code=400, content={
            "error": "No public GPU endpoint yet — set your Cove address first "
                     "(the offer points renters at voice.{your-domain})."})
    from src.dashboard.routes.market import _socrates
    res = await _socrates("POST", "/api/marketplace/gpu/offer", json={
        "title": (body.title or "").strip(),
        "description": (body.description or "").strip(),
        "endpoint": endpoint,
        "price_cents": max(0, int(body.price_cents or 0)),
        "billing_note": (body.billing_note or "").strip(),
        "unit": (body.unit or "per job").strip(),
    })
    return res


@router.post("/api/gpu/offer/withdraw")
async def withdraw_offer(request: Request):
    """Take this Cove's GPU offer off the market. Admin only. Existing grants
    stay under the revoke control above, unchanged."""
    if not await _is_admin(request):
        return JSONResponse(status_code=403, content={"error": "Admin Presence only"})
    from src.dashboard.routes.market import _socrates
    return await _socrates("POST", "/api/marketplace/gpu/offer/withdraw", json={})


@router.get("/api/gpu/offer")
async def my_offer(request: Request):
    """This Cove's own GPU offer state — drives the rent-gpu Offer panel. Admin only."""
    if not await _is_admin(request):
        return JSONResponse(status_code=403, content={"error": "Admin Presence only"})
    from src.dashboard.routes.market import _socrates
    try:
        return await _socrates("GET", "/api/marketplace/gpu/offer/mine")
    except Exception:
        return {"ok": False, "offer": None, "reason": "marketplace unreachable"}


@router.post("/api/gpu/verify")
async def verify_grant(body: TokenBody, request: Request):
    """Internal: pipecat calls this to validate an incoming X-Cove-GPU-Token. Returns
    {ok, grant_id} for an active grant, 401 otherwise. When PIPECAT_INTERNAL_SECRET is set,
    the caller must present it as X-Pipecat-Secret (so this isn't an open oracle)."""
    secret = (env("PIPECAT_INTERNAL_SECRET") or "").strip()
    if secret and request.headers.get("X-Pipecat-Secret") != secret:
        return JSONResponse(status_code=401, content={"ok": False, "error": "internal auth required"})
    token = (body.token or "").strip()
    if not token:
        return JSONResponse(status_code=401, content={"ok": False, "error": "no token"})
    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            result = await conn.execute(
                "UPDATE gpu_grants SET last_used_at = NOW() "
                "WHERE token_hash = %s AND revoked = FALSE RETURNING id",
                (_hash_token(token),),
            )
            row = await result.fetchone()
    except Exception as e:
        logger.error(f"verify_grant failed: {e}")
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})
    if not row:
        return JSONResponse(status_code=401, content={"ok": False, "error": "invalid or revoked"})
    return {"ok": True, "grant_id": row["id"]}


# ── Marketplace auto-grant (C2, the S1 counterpart) ─────────────────────────
# A marketplace sale on the hub calls POST /api/gpu/marketplace-grant with the fleet
# secret to open THIS Cove's GPU for the buyer. It mints a marketplace-scoped grant
# (hash-only, reusing the machinery above), idempotent per (listing, buyer). The
# provider is ALWAYS notified (ntfy if configured + a Reports log line). A per-listing
# "require my approval first" toggle (default OFF, GPU_MARKETPLACE_REQUIRE_APPROVAL)
# mints the grant disabled + pending, surfaced in rent-gpu.html to approve/deny.

def _fleet_secret() -> str:
    return (env("FLEET_SECRET") or env("SHARED_CONTAINER_SECRET") or "").strip()


def _fleet_ok(request: Request, body_secret: str = "") -> bool:
    """The hub proves itself with the fleet secret (header or body). Constant-time."""
    fs = _fleet_secret()
    if not fs:
        return False
    supplied = (request.headers.get("X-Fleet-Secret", "")
                or request.headers.get("X-Shared-Secret", "") or body_secret or "")
    return bool(supplied) and hmac.compare_digest(supplied, fs)


async def _notify_marketplace_grant(listing_id, buyer_handle: str, approval: bool):
    """Always tell the provider a marketplace grant initiated (ntfy best-effort + a
    Reports log line). No structured reports table exists in cove-core, so the Reports
    line goes through the app logger (captured in the MC log buffer / activity)."""
    verb = "pending approval" if approval else "initiated"
    line = f"marketplace grant {verb} — listing {listing_id} for @{buyer_handle}"
    logger.info("[reports] %s", line)                       # the Reports line
    ntfy = (env("COVE_NTFY_URL") or env("NOON_NTFY_URL") or "").strip()
    if not ntfy:
        return
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            await client.post(ntfy, content=line.encode("utf-8"),
                              headers={"Title": "GPU marketplace", "Priority": "default"})
    except Exception as e:
        logger.warning("ntfy marketplace-grant alert skipped: %s", e)


class MarketplaceGrantIn(BaseModel):
    listing_id: int
    buyer_handle: str
    secret: Optional[str] = ""


@router.post("/api/gpu/marketplace-grant")
async def marketplace_grant(body: MarketplaceGrantIn, request: Request):
    """Mint a marketplace-scoped GPU grant for a paid order (called by the hub S1
    webhook). Fleet-secret gated. Idempotent per (listing, buyer): a retry returns the
    existing grant reference (the raw token is shown only on first mint). Returns
    {ok, grant_ref, require_approval}."""
    if not _fleet_ok(request, body.secret or ""):
        return JSONResponse(status_code=403, content={"ok": False, "error": "fleet secret required"})
    buyer = (body.buyer_handle or "").lstrip("@").strip().lower()
    if not buyer:
        return JSONResponse(status_code=400, content={"ok": False, "error": "buyer_handle required"})
    require_approval = env_bool("GPU_MARKETPLACE_REQUIRE_APPROVAL")
    token = "gpugrant_" + secrets.token_hex(24)
    label = f"marketplace: listing {body.listing_id} / @{buyer}"
    approval_status = "pending" if require_approval else None
    revoked = bool(require_approval)   # held disabled until approved
    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            result = await conn.execute(
                """INSERT INTO gpu_grants
                     (token_hash, label, source, listing_id, buyer_handle, approval_status, revoked)
                   VALUES (%s, %s, 'marketplace', %s, %s, %s, %s)
                   ON CONFLICT (listing_id, buyer_handle) WHERE source='marketplace'
                     DO NOTHING
                   RETURNING id""",
                (_hash_token(token), label, int(body.listing_id), buyer, approval_status, revoked),
            )
            row = await result.fetchone()
            if row:
                grant_id, first_mint = row["id"], True
            else:
                # Already granted for this (listing, buyer) — idempotent no-op; return it.
                ex = await conn.execute(
                    "SELECT id, approval_status FROM gpu_grants "
                    "WHERE source='marketplace' AND listing_id=%s AND buyer_handle=%s",
                    (int(body.listing_id), buyer))
                exrow = await ex.fetchone()
                grant_id = exrow["id"] if exrow else None
                approval_status = (exrow or {}).get("approval_status") if exrow else approval_status
                first_mint = False
    except Exception as e:
        logger.error(f"marketplace_grant failed: {e}")
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)[:200]})

    await _notify_marketplace_grant(body.listing_id, buyer, require_approval)
    resp = {"ok": True, "grant_ref": f"mgrant_{grant_id}" if grant_id else "granted",
            "grant_id": grant_id, "require_approval": require_approval,
            "approval_status": approval_status, "endpoint": _public_gpu_endpoint()}
    if first_mint:
        # The raw token is returned ONCE over the fleet-secret channel so the hub can relay
        # it to the buyer's Cove (compute wiring is the next slice). Not stored in the clear.
        resp["token"] = token
    return resp


@router.get("/api/gpu/marketplace-grants")
async def list_marketplace_grants(request: Request):
    """Marketplace grants on this Cove — drives the rent-gpu pending-approval strip.
    Admin only. Pending rows first."""
    if not await _is_admin(request):
        return JSONResponse(status_code=403, content={"error": "Admin Presence only"})
    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            result = await conn.execute(
                "SELECT id, label, listing_id, buyer_handle, approval_status, revoked, "
                "created_at, last_used_at FROM gpu_grants WHERE source='marketplace' "
                "ORDER BY (approval_status='pending') DESC, created_at DESC")
            rows = await result.fetchall()
    except Exception as e:
        logger.warning(f"list_marketplace_grants (table missing?): {e}")
        return {"grants": []}
    return {"grants": list(rows)}


@router.post("/api/gpu/marketplace-grants/{grant_id}/approve")
async def approve_marketplace_grant(grant_id: int, request: Request):
    """Approve a held marketplace grant → enable it (revoked=FALSE). Admin only."""
    if not await _is_admin(request):
        return JSONResponse(status_code=403, content={"error": "Admin Presence only"})
    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            r = await conn.execute(
                "UPDATE gpu_grants SET revoked=FALSE, approval_status='approved' "
                "WHERE id=%s AND source='marketplace' RETURNING id", (grant_id,))
            row = await r.fetchone()
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
    if not row:
        return JSONResponse(status_code=404, content={"error": "grant not found"})
    return {"ok": True, "id": grant_id, "approval_status": "approved"}


@router.post("/api/gpu/marketplace-grants/{grant_id}/deny")
async def deny_marketplace_grant(grant_id: int, request: Request):
    """Deny a held marketplace grant → keep it revoked. Admin only. (Refund/void is an
    operator action on the Stripe side; this just keeps the GPU closed.)"""
    if not await _is_admin(request):
        return JSONResponse(status_code=403, content={"error": "Admin Presence only"})
    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            r = await conn.execute(
                "UPDATE gpu_grants SET revoked=TRUE, approval_status='denied' "
                "WHERE id=%s AND source='marketplace' RETURNING id", (grant_id,))
            row = await r.fetchone()
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
    if not row:
        return JSONResponse(status_code=404, content={"error": "grant not found"})
    return {"ok": True, "id": grant_id, "approval_status": "denied"}
