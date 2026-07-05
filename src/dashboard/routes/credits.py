# =============================================================================
# credits.py — the credit on-ramp endpoint (#128/#169), on the hub.
# =============================================================================
# The first real edge of the credit economy: Stripe (on Socrates) takes a chunky
# top-up payment, then calls THIS endpoint to mint the credits into the buyer's
# wallet via the double-entry ledger. Secret-gated (SHARED_CONTAINER_SECRET, the
# same inter-service secret Socrates already uses) and idempotent (a webhook retry
# re-sends the same idempotency_key → no double-credit).
#
# Internal marketplace settlement and the off-ramp (cashout) are separate; this
# module is just the cash-in edge. Spec: LP-Vault/Reference/commerce-credit-economy-spec.md.
# =============================================================================
import hmac
import os
from src.env import env, env_bool

from fastapi import APIRouter, Request, HTTPException

from src.economy import ledger

router = APIRouter()

SECRET = env("SHARED_CONTAINER_SECRET")


def _require_secret(request: Request, body: dict = None):
    supplied = request.headers.get("X-Shared-Secret", "") or ((body or {}).get("secret") or "")
    if not SECRET:
        raise HTTPException(501, "Credits not configured (SHARED_CONTAINER_SECRET unset)")
    if not (supplied and hmac.compare_digest(supplied, SECRET)):
        raise HTTPException(403, "Invalid secret")


@router.post("/api/credits/topup")
async def topup(request: Request):
    """Mint credits to a wallet after an external (Stripe) top-up payment clears.
    Body: { secret, handle, credits | usd, idempotency_key?, external_ref? }."""
    body = await request.json()
    _require_secret(request, body)

    handle = (body.get("handle") or "").strip().lstrip("@")
    credits = body.get("credits")
    if credits is None and body.get("usd") is not None:
        credits = ledger.usd_to_credits(float(body["usd"]))
    try:
        credits = int(credits)
    except (TypeError, ValueError):
        credits = 0
    if not handle or credits <= 0:
        raise HTTPException(400, "handle and a positive credits/usd amount are required")

    res = await ledger.post(
        "topup", ledger.plan_topup(handle, credits),
        idempotency_key=body.get("idempotency_key"),
        related_handle=handle, gross=credits, external_ref=body.get("external_ref"),
        metadata={"source": "stripe_topup"})
    bal = await ledger.balance(handle)
    return {"ok": True, "handle": handle, "credited": credits, "balance": bal, **res}


@router.post("/api/credits/purchase")
async def purchase(request: Request):
    """Settle a credit-rail marketplace sale (#169). Body:
        { secret, buyer_handle, seller_handle, gross | usd, listing_id?, idempotency_key? }
    Debits the buyer, credits the seller, books LP's fee, and pays the affiliate split
    to whoever recruited the seller — one balanced ledger txn. 402 if the buyer is short."""
    body = await request.json()
    _require_secret(request, body)

    buyer = (body.get("buyer_handle") or "").strip().lstrip("@")
    seller = (body.get("seller_handle") or "").strip().lstrip("@")
    gross = body.get("gross")
    if gross is None and body.get("usd") is not None:
        gross = ledger.usd_to_credits(float(body["usd"]))
    try:
        gross = int(gross)
    except (TypeError, ValueError):
        gross = 0
    if not buyer or not seller or gross <= 0:
        raise HTTPException(400, "buyer_handle, seller_handle and a positive gross/usd are required")

    from src.economy import market
    try:
        res = await market.settle_credit_purchase(
            buyer_handle=buyer, seller_handle=seller, gross=gross,
            listing_id=body.get("listing_id"), idempotency_key=body.get("idempotency_key"))
    except ValueError as e:
        # insufficient funds / bad split → Payment Required (not a server error)
        raise HTTPException(402, str(e))
    return {
        "ok": True, "gross": gross,
        "buyer_balance": await ledger.balance(buyer),
        "seller_balance": await ledger.balance(seller),
        **res,
    }


@router.post("/api/credits/commission")
async def commission(request: Request):
    """Book the seller's affiliate (L1/L2) commission in LPC for an external Stripe-direct
    sale (#169). Body: { secret, seller_handle, base_credits | fee_usd, sale_ref?, idempotency_key? }.
    base = LP's fee on the sale. Idempotent."""
    body = await request.json()
    _require_secret(request, body)
    seller = (body.get("seller_handle") or "").strip().lstrip("@")
    base = body.get("base_credits")
    if base is None and body.get("fee_usd") is not None:
        base = ledger.usd_to_credits(float(body["fee_usd"]))
    try:
        base = int(base)
    except (TypeError, ValueError):
        base = 0
    if not seller or base <= 0:
        raise HTTPException(400, "seller_handle and a positive base_credits/fee_usd are required")
    from src.economy import market
    res = await market.book_affiliate_commission(
        seller_handle=seller, base=base, sale_ref=body.get("sale_ref"),
        idempotency_key=body.get("idempotency_key"))
    return {"ok": True, **res}


@router.get("/api/credits/balance")
async def get_balance(request: Request, handle: str = ""):
    """Service/admin balance read (secret-gated)."""
    _require_secret(request)
    handle = handle.strip().lstrip("@")
    if not handle:
        raise HTTPException(400, "handle is required")
    return {"ok": True, "handle": handle, "balance": await ledger.balance(handle)}


def _is_master() -> bool:
    return env_bool("LP_REGISTRY_MASTER")


async def _wallet_balance(handle: str) -> int:
    """The wallet lives on the hub. On the master, read it in-process; from a Cove,
    fetch it from the hub so a Cove never reads its own empty ledger."""
    if _is_master():
        return await ledger.balance(handle)
    base = env("LP_REGISTRY_URL").rstrip("/")
    if not (base and SECRET):
        return 0
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(base + "/api/credits/balance",
                                  headers={"X-Shared-Secret": SECRET}, params={"handle": handle})
        return int(r.json().get("balance", 0)) if r.status_code == 200 else 0
    except Exception:
        return 0


@router.get("/api/credits/me")
async def my_wallet(request: Request):
    """User-facing wallet for the logged-in Presence — their LPC balance. Session-scoped
    (reads the operator's own handle from the session; no secret). Powers the wallet in
    the Market view."""
    from src.dashboard.routes.presence import get_current_presence
    presence = await get_current_presence(request)
    if not presence:
        raise HTTPException(401, "Sign in to see your wallet")
    handle = (presence.get("username") or "").lstrip("@").lower()
    bal = await _wallet_balance(handle) if handle else 0
    return {"ok": True, "handle": handle, "balance": bal,
            "code": ledger.CREDIT_CODE, "usd": ledger.credits_to_usd(bal)}
