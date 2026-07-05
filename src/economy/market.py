"""
market.py — credit-rail purchase settlement (#128/#169), on the hub.

The counterpart to the existing Stripe-direct marketplace (Socrates `marketplace.py`,
which keeps doing large/service sales as Connect destination charges with LP as
merchant of record). THIS settles the micro/digital credit rail: a buyer spends
credits, the seller is credited, LP takes its fee, and the affiliate split is paid
out of LP's fee to whoever recruited the SELLER — all as one balanced ledger txn.

Attribution is the durable registry edge: registry_handles.referred_by. The affiliate
earns a residual on their recruited seller's sales (spec §7), so the chain is walked
from the SELLER: L1 = who referred the seller, L2 = who referred L1.

Entitlement granting (what the buyer now holds) lives in the marketplace layer; this
module settles the money. A credit-tier checkout = settle here + grant the entitlement.
Spec: LP-Vault/Reference/commerce-credit-economy-spec.md.
"""
from src.economy import ledger


def affiliate_chain(seller: str, referred_by: dict):
    """Pure: given the seller handle and a {handle: referrer_handle} map, return
    (l1, l2) — the two affiliate levels above the seller — with self-referral and
    immediate cycles guarded out (never pay a handle on its own sale or double-pay)."""
    l1 = referred_by.get(seller) or None
    if l1 == seller:
        l1 = None
    l2 = (referred_by.get(l1) or None) if l1 else None
    if l2 in (seller, l1):
        l2 = None
    return l1, l2


async def _referred_by(conn, handle: str):
    if not handle:
        return None
    r = await conn.execute(
        "SELECT referred_by FROM registry_handles WHERE lower(handle) = lower(%s)", (handle,))
    row = await r.fetchone()
    return (row["referred_by"] if row else None) or None


async def settle_credit_purchase(*, buyer_handle: str, seller_handle: str, gross: int,
                                 listing_id: str = None, idempotency_key: str = None) -> dict:
    """Settle a credit-rail sale through the ledger. Resolves the seller's referral
    chain, then posts a single balanced txn (buyer -gross, seller +net, LP fee,
    affiliate L1/L2 out of the fee). Raises ValueError on insufficient buyer funds
    (the ledger's no-negative guard) or a misconfigured split. Idempotent via key."""
    from src.memory.database import get_db
    buyer = (buyer_handle or "").strip().lstrip("@")
    seller = (seller_handle or "").strip().lstrip("@")
    gross = int(gross)
    if not buyer or not seller:
        raise ValueError("buyer_handle and seller_handle are required")
    if buyer == seller:
        raise ValueError("buyer and seller cannot be the same handle")

    async with get_db() as conn:
        l1 = await _referred_by(conn, seller)
        l2 = await _referred_by(conn, l1) if l1 else None
    # guard self / immediate cycles (a referral edge should never pay the seller or L1 twice)
    if l1 == seller:
        l1 = None
    if l2 in (seller, l1):
        l2 = None

    entries = ledger.plan_purchase(
        buyer_handle=buyer, seller_handle=seller, gross=gross,
        l1_handle=l1, l2_handle=l2)
    res = await ledger.post(
        "purchase", entries, idempotency_key=idempotency_key,
        source_handle=buyer, related_handle=seller, listing_id=listing_id, gross=gross,
        metadata={"l1": l1, "l2": l2})
    return {"l1": l1, "l2": l2, **res}


async def book_affiliate_commission(*, seller_handle: str, base: int,
                                    sale_ref: str = None, idempotency_key: str = None) -> dict:
    """Pay the seller's affiliate (L1/L2) in LPC for an external (Stripe-direct) sale —
    so an affiliate earns regardless of which rail the sale settled on. `base` = LP's fee
    on the sale (credits). Resolves the seller's referral chain and books the commission."""
    from src.memory.database import get_db
    seller = (seller_handle or "").strip().lstrip("@").lower()
    if not seller or int(base) <= 0:
        return {"booked": False, "reason": "no seller or zero base"}
    async with get_db() as conn:
        l1 = await _referred_by(conn, seller)
        l2 = await _referred_by(conn, l1) if l1 else None
        if l1 == seller:
            l1 = None
        if l2 in (seller, l1):
            l2 = None
        entries = ledger.plan_commission(l1_handle=l1, l2_handle=l2, base=int(base))
        if not entries:
            return {"booked": False, "l1": l1, "l2": l2}  # no referrer to pay
        res = await ledger.apply_entries(
            conn, "commission", entries, idempotency_key=idempotency_key,
            related_handle=seller, listing_id=sale_ref, gross=int(base),
            metadata={"l1": l1, "l2": l2, "external": True})
    return {"booked": True, "l1": l1, "l2": l2, **res}
