"""
hire.py — the Hire layer (#169), on the hub, on top of the escrow primitives.

A Hire is pay-now-deliver-later (vs Install = instant artifact):

    request ──hold──▶ requested ──▶ accepted ──▶ delivered ──release──▶ released
                          │              │
                          └────cancel────┴──refund──▶ cancelled

The buyer's credits are HELD in escrow at request (ledger.plan_hold), RELEASED
to the seller on delivery with the same fee + affiliate split as a sale
(ledger.plan_release walking the seller's referral chain), or REFUNDED to the
buyer on cancel (ledger.plan_refund). Seller may be a human OR an agent handle.

All ledger posts are idempotent by hire id, so retries are safe.
"""

from src.economy import ledger, market

# state -> the actions allowed from it (and who may take them)
_TRANSITIONS = {
    "accept":  ("requested", "accepted", "seller"),
    "deliver": ("accepted", "delivered", "seller"),
    "release": ("delivered", "released", "buyer"),
}


def _clean(h: str) -> str:
    return (h or "").strip().lstrip("@").lower()


async def open_hire(*, buyer_handle: str, seller_handle: str, amount_credits: int,
                    title: str = "", listing_ref: str = None) -> dict:
    """Start a Hire: record it and HOLD the buyer's credits in escrow.
    Raises ValueError on bad input or insufficient funds (no-negative guard)."""
    from src.memory.database import get_db
    buyer, seller = _clean(buyer_handle), _clean(seller_handle)
    amount = int(amount_credits)
    if not buyer or not seller:
        raise ValueError("buyer and seller handles are required")
    if buyer == seller:
        raise ValueError("you cannot hire yourself")
    if amount <= 0:
        raise ValueError("amount must be positive")

    async with get_db() as conn:
        r = await conn.execute(
            """INSERT INTO hire_requests
                 (buyer_handle, seller_handle, title, listing_ref, amount_credits, state)
               VALUES (%s, %s, %s, %s, %s, 'requested') RETURNING id""",
            (buyer, seller, title or "", listing_ref, amount))
        hire_id = (await r.fetchone())["id"]
    try:
        await ledger.post(
            "hire_hold", ledger.plan_hold(buyer, amount),
            idempotency_key=f"hire-hold-{hire_id}",
            source_handle=buyer, related_handle=seller, gross=amount,
            metadata={"hire_id": hire_id})
    except ValueError:
        async with get_db() as conn:   # couldn't fund — undo the record
            await conn.execute("DELETE FROM hire_requests WHERE id = %s", (hire_id,))
        raise
    return {"hire_id": hire_id, "state": "requested", "amount_credits": amount}


async def act_on_hire(*, hire_id: int, actor_handle: str, action: str,
                      delivery_ref: str = None) -> dict:
    """Advance a Hire. action in {accept, deliver, release, cancel}. Enforces the
    state machine + who's allowed, and runs the escrow side effect for
    release/cancel. PermissionError if the actor isn't allowed."""
    from src.memory.database import get_db
    actor = _clean(actor_handle)
    async with get_db() as conn:
        r = await conn.execute("SELECT * FROM hire_requests WHERE id = %s", (hire_id,))
        h = await r.fetchone()
    if not h:
        raise ValueError("hire not found")
    state, buyer, seller = h["state"], h["buyer_handle"], h["seller_handle"]
    amount = int(h["amount_credits"])

    if action in _TRANSITIONS:
        need_state, new_state, who = _TRANSITIONS[action]
        if state != need_state:
            raise ValueError(f"cannot {action} a hire that is '{state}'")
        if actor != (buyer if who == "buyer" else seller):
            raise PermissionError(f"only the {who} can {action} this hire")
        if action == "release":
            async with get_db() as conn:
                l1 = await market._referred_by(conn, seller)
                l2 = await market._referred_by(conn, l1) if l1 else None
            if l1 == seller:
                l1 = None
            if l2 in (seller, l1):
                l2 = None
            await ledger.post(
                "hire_release",
                ledger.plan_release(seller_handle=seller, amount=amount,
                                    l1_handle=l1, l2_handle=l2),
                idempotency_key=f"hire-release-{hire_id}",
                related_handle=seller, gross=amount,
                metadata={"hire_id": hire_id, "l1": l1, "l2": l2})
    elif action == "cancel":
        if actor not in (buyer, seller):
            raise PermissionError("only the buyer or seller can cancel")
        if state not in ("requested", "accepted"):
            raise ValueError(f"cannot cancel a hire that is '{state}'")
        new_state = "cancelled"
        await ledger.post(
            "hire_refund", ledger.plan_refund(buyer, amount),
            idempotency_key=f"hire-refund-{hire_id}",
            related_handle=buyer, gross=amount, metadata={"hire_id": hire_id})
    else:
        raise ValueError(f"unknown action '{action}'")

    async with get_db() as conn:
        await conn.execute(
            "UPDATE hire_requests SET state = %s, "
            "delivery_ref = COALESCE(%s, delivery_ref), updated_at = NOW() WHERE id = %s",
            (new_state, delivery_ref, hire_id))
    return {"hire_id": hire_id, "state": new_state}


async def list_hires(*, handle: str, role: str = "buyer") -> list[dict]:
    """A handle's hires as buyer (their orders) or seller (their Work inbox)."""
    from src.memory.database import get_db
    h = _clean(handle)
    col = "seller_handle" if role == "seller" else "buyer_handle"
    async with get_db() as conn:
        r = await conn.execute(
            f"""SELECT id, buyer_handle, seller_handle, title, listing_ref,
                       amount_credits, state, delivery_ref, created_at, updated_at
                FROM hire_requests WHERE {col} = %s
                ORDER BY updated_at DESC LIMIT 100""", (h,))
        rows = await r.fetchall()
    out = []
    for row in rows:
        d = dict(row)
        for k, v in d.items():
            if hasattr(v, "isoformat"):
                d[k] = v.isoformat()
        out.append(d)
    return out
