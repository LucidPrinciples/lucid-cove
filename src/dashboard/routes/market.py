"""
market.py — Marketplace proxy (browser-facing) for the MC (#128).

Connect's Market view calls these; cove-core forwards to the Socrates marketplace
API (MARKETPLACE_API_URL) with the shared secret, so the secret never reaches the
browser. The catalog is the OPEN Haven-wide commons (network-wide list). Buy/sell
resolve the logged-in Operator to their Socrates customer_id via the idempotent
/member upsert (external_id = account id), so every member can transact.

Gated by OperatorAuthMiddleware (/api/* needs the operator session in multi mode).
Inert where MARKETPLACE_API_URL isn't set (returns 503) — e.g. single-mode Coves
until they're wired to the hub marketplace.
"""
import os
from src.env import env, env_bool
from typing import List, Optional

import httpx
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

from src.dashboard.routes.presence import get_current_presence

router = APIRouter()

MARKET_URL = env("MARKETPLACE_API_URL")
MARKET_SECRET = env("SHARED_CONTAINER_SECRET")


def _credits_enabled() -> bool:
    """Launch rail flag (marketplace-v1): the credit rail is PLUMBED but routed around
    at launch. Default OFF → wallet/credits tiers/deposit/Hire surfaces hidden, buy
    routing offers fixed-price checkout + free-grant only. Flip ON for the v1.1 metering
    layer. The economy code + tests stay intact either way."""
    return env_bool("MARKETPLACE_CREDITS_ENABLED")


def _cove_base() -> str:
    """This Cove's public base URL (https://<domain>) for building Stripe return URLs.
    Empty when the address isn't set yet (single-mode/pre-claim) — the caller falls back
    to the hub's default return."""
    try:
        from src.config import load_cove_config
        dom = (load_cove_config().get("domain") or "").strip()
        return f"https://{dom}" if dom else ""
    except Exception:
        return ""


def _operator_token() -> str:
    """Operator token for Market auth. Env wins (provisioned/co-located), but a
    from-scratch self-host MINTS its token at runtime and stores it in cove.yaml — read
    that as the fallback so the Market authenticates without a restart. Same source the
    registry client uses."""
    try:
        from src.dashboard.routes.registry_client import _operator_token as _ot
        return _ot()
    except Exception:
        return (env("LP_OPERATOR_TOKEN") or "").strip()


async def _socrates(method: str, path: str, json=None, params=None):
    if not MARKET_URL:
        raise HTTPException(503, "Marketplace not available here")
    # Send BOTH: the fleet secret (founder/co-located) AND the operator token (a member
    # Cove proving it's registered). The hub accepts either — so a provisioned Cove
    # browses the Market without holding the fleet secret (#200).
    headers = {}
    if MARKET_SECRET:
        headers["X-Shared-Secret"] = MARKET_SECRET
    _tok = _operator_token()
    if _tok:
        headers["X-Operator-Token"] = _tok
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.request(method, MARKET_URL.rstrip("/") + path,
                                     headers=headers, json=json, params=params)
    except Exception as e:
        raise HTTPException(502, "Marketplace unreachable: %s" % str(e)[:120])
    if r.status_code >= 400:
        raise HTTPException(r.status_code, "Marketplace error")
    return r.json()


async def _current_presence_and_customer(request: Request):
    """Resolve the logged-in Operator → (presence, customer_id, handle). The member
    upsert now carries the handle so the seller_handle/buyer_handle the credit ledger
    keys on are available marketplace-side."""
    presence = await get_current_presence(request)
    if not presence:
        raise HTTPException(401, "Sign in to use the marketplace")
    handle = (presence.get("username") or "").lstrip("@").lower()
    data = await _socrates("POST", "/api/marketplace/member", json={
        "external_id": str(presence["id"]),
        "email": presence.get("email"),
        "name": presence.get("display_name") or presence.get("username"),
        "handle": handle,
    })
    return presence, data["customer_id"], handle


async def _current_customer_id(request: Request) -> int:
    _, cid, _ = await _current_presence_and_customer(request)
    return cid


async def _settle_credit_on_hub(*, buyer_handle: str, seller_handle: str, gross: int,
                                listing_ref: str, idempotency_key: str) -> dict:
    """Settle a credit-rail sale on the hub ledger. In-process when THIS instance is
    the registry master (the hub, where the wallet DB lives); otherwise over HTTP to
    the hub so a Cove never settles against its own empty ledger."""
    if env_bool("LP_REGISTRY_MASTER"):
        from src.economy.market import settle_credit_purchase
        try:
            return await settle_credit_purchase(
                buyer_handle=buyer_handle, seller_handle=seller_handle, gross=gross,
                listing_id=listing_ref, idempotency_key=idempotency_key)
        except ValueError as e:
            raise HTTPException(402, str(e))
    base = env("LP_REGISTRY_URL").rstrip("/")
    if not (base and MARKET_SECRET):
        raise HTTPException(503, "Credit settlement unavailable (no hub configured)")
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(base + "/api/credits/purchase",
                              headers={"X-Shared-Secret": MARKET_SECRET},
                              json={"secret": MARKET_SECRET, "buyer_handle": buyer_handle,
                                    "seller_handle": seller_handle, "gross": gross,
                                    "listing_id": listing_ref, "idempotency_key": idempotency_key})
    if r.status_code == 402:
        raise HTTPException(402, "Insufficient credits — top up your wallet")
    if r.status_code >= 400:
        raise HTTPException(502, "Credit settlement failed")
    return r.json()


# ── Browse the open commons (no identity needed) ──
@router.get("/api/market/catalog")
async def market_catalog(request: Request):
    return await _socrates("GET", "/api/marketplace/listings")


async def _seller_facets(handles):
    """Seller match-facets (skills/archetype/frequency) from the hub. In-process on the
    master; otherwise POST to the hub so search works from a Cove too."""
    handles = [h for h in handles if h]
    if not handles:
        return {}
    if env_bool("LP_REGISTRY_MASTER"):
        from src.dashboard.routes.profile import _facets_for_handles
        from src.memory.database import get_db
        async with get_db() as conn:
            return await _facets_for_handles(conn, handles)
    base = env("LP_REGISTRY_URL").rstrip("/")
    if not (base and MARKET_SECRET):
        return {}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(base + "/api/profile/facets",
                                  headers={"X-Shared-Secret": MARKET_SECRET},
                                  json={"secret": MARKET_SECRET, "handles": handles})
        return r.json().get("facets", {}) if r.status_code == 200 else {}
    except Exception:
        return {}


@router.get("/api/market/search")
async def market_search(request: Request, q: str = "", skill: str = "",
                        archetype: str = "", frequency: str = "", rail: str = ""):
    """Faceted marketplace search (the matchmaking lens): full catalog enriched with each
    seller's skills + agent archetype/frequency, then filtered. Returns the matches plus
    the available facet values so the UI can offer them. Empty params = browse all."""
    data = await _socrates("GET", "/api/marketplace/listings")
    listings = data.get("listings", []) or []
    handles = list({(l.get("seller_handle") or "").lower() for l in listings if l.get("seller_handle")})
    facets = await _seller_facets(handles)
    for l in listings:
        f = facets.get((l.get("seller_handle") or "").lower(), {})
        l["seller_skills"] = f.get("skills") or []
        l["agent_archetype"] = f.get("archetype") or ""
        l["agent_frequency"] = f.get("frequency") or ""

    ql = (q or "").lower().strip()
    sk = (skill or "").strip()
    arch = (archetype or "").strip().lower()
    freq = (frequency or "").strip().lower()
    rl = (rail or "").strip().lower()

    def match(l):
        if ql:
            hay = (l.get("title", "") + " " + (l.get("description") or "") + " " +
                   " ".join(l.get("seller_skills", []))).lower()
            if ql not in hay:
                return False
        if sk and sk not in (l.get("seller_skills") or []):
            return False
        if arch and arch != (l.get("agent_archetype") or "").lower():
            return False
        if freq and freq != (l.get("agent_frequency") or "").lower():
            return False
        if rl in ("credits", "stripe"):
            if not any((t.get("settlement", "stripe") == rl) for t in (l.get("tiers") or [])):
                return False
        return True

    filtered = [l for l in listings if match(l)]
    avail = {
        "skills": sorted({s for l in listings for s in (l.get("seller_skills") or [])}),
        "archetypes": sorted({l.get("agent_archetype") for l in listings if l.get("agent_archetype")}),
        "frequencies": sorted({l.get("agent_frequency") for l in listings if l.get("agent_frequency")}),
    }
    return {"listings": filtered, "facets": avail, "total": len(listings), "shown": len(filtered)}


# ── Buy a tier (free → granted now; paid → Stripe checkout_url) ──
class BuyIn(BaseModel):
    tier_id: int


# Appointment-type items fulfill into a "coordinate a time" Connect thread (#190).
_APPOINTMENT_TYPES = {"service", "appointment", "call", "booking"}


def _fulfill_meta(tier: dict) -> dict:
    """Extra fields the frontend uses to fulfill a purchase. For appointment-type items
    the buyer opens a Connect thread with the seller to set a time."""
    if (tier.get("product_type") or "") in _APPOINTMENT_TYPES:
        return {"fulfillment": "appointment",
                "seller_handle": (tier.get("seller_handle") or "").lstrip("@").lower(),
                "item_title": tier.get("title")}
    return {}


def _market_success_url(tier: dict) -> str:
    """Where checkout returns the buyer: their active Tools card (C3). No wallet page.
    For a GPU purchase that's the GPU marketplace 'your access' surface; otherwise the
    Action Board Tools tab. Empty base → let the hub use its default return."""
    base = _cove_base()
    if not base:
        return ""
    if (tier.get("product_type") or "") == "gpu":
        return f"{base}/static/action-board/gpu-marketplace.html?embedded=1&status=success"
    return f"{base}/?tab=tools&purchased=1"


@router.post("/api/market/buy")
async def market_buy(body: BuyIn, request: Request):
    """Buy a tier. Launch rail (marketplace-v1): free → grant; fixed-price → the
    simplified Stripe checkout (S1, LP the only vendor, no split); credits → only when
    MARKETPLACE_CREDITS_ENABLED is ON (routed around at launch, plumbing intact).
    """
    presence, cid, buyer_handle = await _current_presence_and_customer(request)
    tier = await _socrates("GET", f"/api/marketplace/tier/{body.tier_id}")

    price_cents = int(tier.get("price_cents") or 0)
    price_credits = int(tier.get("price_credits") or 0)
    settlement = (tier.get("settlement") or "stripe").lower()

    # Free → grant immediately via the existing checkout path.
    if price_cents <= 0 and price_credits <= 0:
        res = await _socrates("POST", "/api/marketplace/checkout",
                              json={"buyer_id": cid, "tier_id": body.tier_id})
        return {**res, **_fulfill_meta(tier)}

    # Credit rail → only when the flag is ON. At launch it's routed around: a credits-only
    # tier isn't buyable here (the plumbing + tests stay intact; this is a routing guard,
    # not a removal). When ON, settle on the hub ledger then grant.
    if settlement == "credits":
        if not _credits_enabled():
            raise HTTPException(
                400, "The credit rail is off at launch — this item needs a fixed-price "
                     "tier to be purchasable right now.")
        seller_handle = (tier.get("seller_handle") or "").lstrip("@").lower()
        if not seller_handle or price_credits <= 0:
            raise HTTPException(400, "This item isn't purchasable with credits")
        if seller_handle == buyer_handle:
            raise HTTPException(400, "You can't buy your own listing")
        settle = await _settle_credit_on_hub(
            buyer_handle=buyer_handle, seller_handle=seller_handle, gross=price_credits,
            listing_ref=str(tier.get("listing_id")),
            idempotency_key=f"buy_{cid}_{body.tier_id}")
        grant = await _socrates("POST", "/api/marketplace/grant",
                                json={"buyer_id": cid, "tier_id": body.tier_id, "source": "credit"})
        return {"granted": True, "rail": "credits", "entitlement_key": grant.get("entitlement_key"),
                "spent_credits": price_credits, "settlement": settle, **_fulfill_meta(tier)}

    # Fixed-price rail. At launch (credits off) LP is the only vendor → the simplified
    # market-checkout (S1: plain Stripe Checkout, no Connect split, $12 floor enforced
    # server-side). When the credit rail (and with it the third-party split era) is ON,
    # keep the original Connect destination-charge checkout — route-around, not rip-out.
    if _credits_enabled():
        return await _socrates("POST", "/api/marketplace/checkout",
                               json={"buyer_id": cid, "tier_id": body.tier_id})
    payload = {"buyer_id": cid, "tier_id": body.tier_id, "buyer_handle": buyer_handle}
    success_url = _market_success_url(tier)
    if success_url:
        payload["success_url"] = success_url
    res = await _socrates("POST", "/api/commerce/market-checkout", json=payload)
    return {**res, **_fulfill_meta(tier)}


@router.get("/api/market/config")
async def market_config(request: Request):
    """Launch-rail UI flags the frontend reads to hide credits/wallet surfaces (C1).
    Public (no identity) — just booleans + the posted-rate pointer."""
    return {
        "credits_enabled": _credits_enabled(),
        "min_price_cents": int(env("MARKET_MIN_PRICE_CENTS") or 1200),
        "wallet_visible": _credits_enabled(),
        "hire_visible": _credits_enabled(),
    }


# ── Claim a "wanted" gap to build it (#169) ──
class ClaimIn(BaseModel):
    listing_id: int


@router.post("/api/market/claim")
async def market_claim(body: ClaimIn, request: Request):
    cid = await _current_customer_id(request)
    return await _socrates("POST", "/api/marketplace/claim",
                           json={"listing_id": body.listing_id, "claimer_id": cid})


# ── Everything the current Operator owns (their library → Action Board) ──
@router.get("/api/market/library")
async def market_library(request: Request):
    cid = await _current_customer_id(request)
    return await _socrates("GET", "/api/marketplace/library", params={"buyer_id": cid})


# ── Does the current Operator hold an entitlement? ──
@router.get("/api/market/entitlement")
async def market_entitlement(key: str, request: Request):
    cid = await _current_customer_id(request)
    return await _socrates("GET", "/api/marketplace/entitlement",
                           params={"buyer_id": cid, "key": key})


# ── Sell: list a tool ──
class SellTier(BaseModel):
    name: str
    price_cents: int = 0           # Stripe-direct rail (large/service)
    price_credits: int = 0         # credit rail (micro/digital)
    settlement: str = "stripe"     # "stripe" | "credits"
    entitlement_key: str
    description: Optional[str] = None


class SellIn(BaseModel):
    slug: str
    title: str
    description: Optional[str] = None
    product_type: str = "download"
    delivery_ref: Optional[str] = None
    image_url: Optional[str] = None
    tiers: List[SellTier]


async def _is_tuning_compliant(days: int = 7) -> bool:
    """#128 Tuned + Safe — has this Cove tuned within `days`? The marketplace
    lever that makes the Haven a tuning field: sellers must be tuning. Hopeful
    v1 checks the Cove's own recent tuning (echoes); cross-Cove proof is a
    follow-up. Fails open=False (no badge) rather than blocking the sale."""
    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            r = await conn.execute(
                "SELECT MAX(tuned_at) AS last FROM echoes "
                "WHERE tuned_at >= NOW() - make_interval(days => %s)",
                (max(1, days),),
            )
            row = await r.fetchone()
            return bool(row and row.get("last"))
    except Exception as e:
        print(f"[tuned_safe] compliance check failed (non-fatal): {e}")
        return False


# ── Stock the wanted board (M46): an idea card the Haven can take over ──
class WantedProxyIn(BaseModel):
    slug: str = ""
    title: str
    description: str = ""
    product_type: str = "tool"
    category: Optional[str] = None
    spec_ref: Optional[str] = None
    issue_url: Optional[str] = None


@router.post("/api/market/wanted")
async def market_wanted(body: WantedProxyIn, request: Request):
    """Proxy to the hub's fleet-gated wanted-upsert. Requires an operator session
    on this Cove; the hub's own gate decides whether this Cove may stock the
    shared board (member Coves without the fleet secret get the hub's 403)."""
    await _current_customer_id(request)  # session required — anonymous never reaches the hub
    import re as _re
    slug = (body.slug or _re.sub(r"[^a-z0-9]+", "-", body.title.lower()).strip("-"))[:60]
    return await _socrates("POST", "/api/marketplace/wanted", json={
        "slug": slug, "title": body.title, "description": body.description,
        "product_type": body.product_type, "category": body.category,
        "spec_ref": body.spec_ref, "issue_url": body.issue_url,
    })


@router.post("/api/market/sell")
async def market_sell(body: SellIn, request: Request):
    _, cid, seller_handle = await _current_presence_and_customer(request)
    tuned_safe = await _is_tuning_compliant()
    tiers = [{"name": t.name, "price_cents": t.price_cents,
              "price_credits": t.price_credits, "settlement": t.settlement,
              "entitlement_key": t.entitlement_key, "description": t.description}
             for t in body.tiers]
    payload = {"seller_id": cid, "slug": body.slug, "title": body.title,
               "description": body.description, "product_type": body.product_type,
               "delivery_ref": body.delivery_ref, "image_url": body.image_url,
               "tuned_safe": tuned_safe, "tiers": tiers}
    result = await _socrates("POST", "/api/marketplace/listings", json=payload)
    # A seller must be resolvable cross-Cove (#173) — mirror them to the hub on listing.
    try:
        from src.dashboard.routes.profile import sync_profile_mirror
        await sync_profile_mirror(seller_handle)
    except Exception:
        pass
    return result


# ── Your own listings (My Offerings — all statuses incl. claimed drafts) ──
@router.get("/api/market/mine")
async def market_mine(request: Request):
    cid = await _current_customer_id(request)
    return await _socrates("GET", "/api/marketplace/mine", params={"seller_id": cid})


# ── My Offerings management (#175): publish a draft / edit / unlist your own listing ──
@router.post("/api/market/mine/status")
async def market_mine_status(request: Request):
    cid = await _current_customer_id(request)
    body = await request.json()
    return await _socrates("POST", "/api/marketplace/listing/status",
                           json={"listing_id": body.get("listing_id"), "seller_id": cid,
                                 "status": body.get("status")})


@router.post("/api/market/mine/edit")
async def market_mine_edit(request: Request):
    cid = await _current_customer_id(request)
    body = await request.json()
    payload = dict(body)
    payload["seller_id"] = cid   # never trust a client-supplied seller id
    return await _socrates("POST", "/api/marketplace/listing/edit", json=payload)


# ── Stripe Connect onboarding (#178) — needed before selling a cash/Stripe tier ──
# Browser-facing proxies that resolve the logged-in Operator → customer_id and forward
# to the secret-gated Connect endpoints on Socrates (commerce.py). The browser never
# sees the shared secret. The Sell panel checks status, and on "not onboarded" sends
# the seller through Stripe's hosted onboarding instead of a raw 400.
@router.get("/api/market/connect/status")
async def market_connect_status(request: Request):
    cid = await _current_customer_id(request)
    return await _socrates("GET", f"/api/commerce/connect/status/{cid}")


@router.post("/api/market/connect/onboard")
async def market_connect_onboard(request: Request):
    cid = await _current_customer_id(request)
    return await _socrates("POST", f"/api/commerce/connect/onboard/{cid}")
