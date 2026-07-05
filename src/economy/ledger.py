"""
ledger.py — the internal credit ledger (#128), the spine of the credit economy.

DOUBLE-ENTRY, INTEGER CREDITS. Every economic event is a `txn` whose `ledger_entries`
deltas net to exactly zero. External money (Stripe, later crypto) touches the ledger
only at the two edges — `topup` (cash in → credits minted) and `payout` (credits →
cash out). Everything between (marketplace purchases, the platform fee, the affiliate
split) is pure internal credit movement: no payment processor, no per-sale fee.

Why double-entry and not a single balance column: the outstanding credit float is a
real liability (money owed), so it must be provable to the cent and reconcilable. The
denormalized `wallets.balance` is a fast read kept in lockstep with the entries; the
entries are the source of truth.

Design split:
  - PURE planning functions (`plan_topup`/`plan_purchase`/`plan_payout`) compute a
    balanced set of entries. No DB, no I/O — unit-testable in isolation.
  - `post()` persists a balanced entry set atomically (one transaction) with an
    idempotency key so a webhook retry never double-credits.

Spec: LP-Vault/Reference/commerce-credit-economy-spec.md (§4 model, §6 pricing, §7 split).
Credit unit: 1 credit = $0.01 (CREDIT_CENTS). Rates are env-tunable (open decisions).
"""
import json
import os
from src.env import env_float
import uuid
from dataclasses import dataclass

# 1 credit = $0.01. Integer credits everywhere — never float money.
CREDIT_CENTS = 1
# The currency: LPC = Lucid Principles Credit. Network-wide (Tuner/Cove/Haven/market),
# named for the umbrella brand, not the single product — and avoids the "Lucid
# Credits / LC" trademark exposure. Display: "X LPC".
CREDIT_CODE = "LPC"
CREDIT_NAME = "Lucid Principles Credits"

# System wallets (not registry handles; allowed to go negative).
SYS_LP_FEES = "_lp_fees"   # LP's accrued platform fee (revenue)
SYS_ISSUED = "_issued"     # credits in circulation = liability; topup -, payout +
SYS_ESCROW = "_escrow"     # Hire-layer funds held until work is delivered (hold/release/refund)

# Tunable rates (OPEN decisions — see spec §16). Platform fee = fraction of GMV;
# affiliate L1/L2 = fraction of LP's NET fee (so LP can never go negative on a sale).
PLATFORM_FEE_RATE = env_float("LP_PLATFORM_FEE_RATE", "0.10")
AFFILIATE_L1_RATE = env_float("LP_AFFILIATE_L1_RATE", "0.30")
AFFILIATE_L2_RATE = env_float("LP_AFFILIATE_L2_RATE", "0.10")


@dataclass(frozen=True)
class Entry:
    """One side of a double-entry transaction. delta: +credit / -debit (integer)."""
    handle: str
    delta: int
    kind: str


def usd_to_credits(usd: float) -> int:
    return round(usd * 100 / CREDIT_CENTS)


def credits_to_usd(credits: int) -> float:
    return credits * CREDIT_CENTS / 100.0


def _assert_balanced(entries):
    s = sum(e.delta for e in entries)
    if s != 0:
        raise ValueError(f"unbalanced ledger txn: entries net to {s}, not 0")
    return entries


# ── Pure planners (no I/O) ───────────────────────────────────────────────────

def plan_topup(buyer_handle: str, credits: int):
    """Cash in: mint `credits` to the buyer, drawn against the issued/liability wallet."""
    if credits <= 0:
        raise ValueError("topup credits must be positive")
    return _assert_balanced([
        Entry(buyer_handle, credits, "topup"),
        Entry(SYS_ISSUED, -credits, "topup"),
    ])


def plan_purchase(*, buyer_handle: str, seller_handle: str, gross: int,
                  fee_rate: float = None, l1_handle: str = None, l2_handle: str = None,
                  l1_rate: float = None, l2_rate: float = None, external_cost: int = 0):
    """A marketplace sale settled in credits. Seller keeps gross - fee. The affiliate
    split comes OUT OF LP's fee (never the seller, never on top), computed on LP's NET
    fee (fee - any external_cost), and LP's residual must stay >= 0.

    Returns balanced entries: buyer -gross, seller +(gross-fee), LP +lp_keep,
    and (if referred) affiliate L1 / L2 out of the fee.
    """
    if gross <= 0:
        raise ValueError("purchase gross must be positive")
    fee_rate = PLATFORM_FEE_RATE if fee_rate is None else fee_rate
    l1_rate = AFFILIATE_L1_RATE if l1_rate is None else l1_rate
    l2_rate = AFFILIATE_L2_RATE if l2_rate is None else l2_rate

    fee = round(gross * fee_rate)
    net_fee = fee - max(0, external_cost)            # affiliate base = LP's net fee
    l1 = round(net_fee * l1_rate) if l1_handle else 0
    l2 = round(net_fee * l2_rate) if l2_handle else 0
    lp_keep = fee - l1 - l2
    if lp_keep < 0:
        raise ValueError(
            f"affiliate split (L1 {l1} + L2 {l2}) exceeds platform fee ({fee}) — "
            f"LP would go negative; lower the affiliate rates or raise the fee")
    seller_credit = gross - fee

    entries = [
        Entry(buyer_handle, -gross, "purchase"),
        Entry(seller_handle, seller_credit, "purchase"),
        Entry(SYS_LP_FEES, lp_keep, "platform_fee"),
    ]
    if l1_handle and l1:
        entries.append(Entry(l1_handle, l1, "affiliate_l1"))
    if l2_handle and l2:
        entries.append(Entry(l2_handle, l2, "affiliate_l2"))
    return _assert_balanced(entries)


def plan_commission(*, l1_handle: str = None, l2_handle: str = None, base: int,
                     l1_rate: float = None, l2_rate: float = None):
    """Book affiliate commission in LPC for an EXTERNAL (Stripe-direct) sale — the buyer's
    money already moved off-ledger, so only the affiliate cut is recorded here. LP issues
    LPC to the affiliates against the real-cash fee it collected (drawn from the issued/
    liability wallet). `base` = LP's fee on the sale (credits). Returns balanced entries,
    or [] when there's no referrer to pay (caller skips posting)."""
    base = int(base)
    if base <= 0:
        return []
    l1_rate = AFFILIATE_L1_RATE if l1_rate is None else l1_rate
    l2_rate = AFFILIATE_L2_RATE if l2_rate is None else l2_rate
    l1 = round(base * l1_rate) if l1_handle else 0
    l2 = round(base * l2_rate) if l2_handle else 0
    entries = []
    if l1_handle and l1:
        entries.append(Entry(l1_handle, l1, "affiliate_l1"))
    if l2_handle and l2:
        entries.append(Entry(l2_handle, l2, "affiliate_l2"))
    if not entries:
        return []
    entries.append(Entry(SYS_ISSUED, -(l1 + l2), "affiliate_payout"))
    return _assert_balanced(entries)


# ── Hire layer: escrow (hold → release / refund) ────────────────────────────
# A Hire is "pay now, deliver later": the buyer's credits are HELD in escrow when the
# engagement starts, then RELEASED to the seller (with fee + affiliate, same economics
# as a sale) once work is delivered — or REFUNDED to the buyer if it's cancelled. This
# is what distinguishes Hire from Install (instant artifact). Seller may be a human OR
# an agent handle (agents are hireable economic actors).

def plan_hold(buyer_handle: str, credits: int):
    """Hold the buyer's credits in escrow at the start of a Hire engagement."""
    if credits <= 0:
        raise ValueError("hold must be positive")
    return _assert_balanced([
        Entry(buyer_handle, -credits, "hold"),
        Entry(SYS_ESCROW, credits, "hold"),
    ])


def plan_release(*, seller_handle: str, amount: int, fee_rate: float = None,
                 l1_handle: str = None, l2_handle: str = None,
                 l1_rate: float = None, l2_rate: float = None, external_cost: int = 0):
    """Release held escrow to the seller on delivery — same split as a sale (seller keeps
    amount - fee; LP keeps its fee minus the affiliate L1/L2 out of the net fee). The
    debit source is escrow, not the buyer (the buyer already paid into escrow at hold)."""
    amount = int(amount)
    if amount <= 0:
        raise ValueError("release amount must be positive")
    fee_rate = PLATFORM_FEE_RATE if fee_rate is None else fee_rate
    l1_rate = AFFILIATE_L1_RATE if l1_rate is None else l1_rate
    l2_rate = AFFILIATE_L2_RATE if l2_rate is None else l2_rate
    fee = round(amount * fee_rate)
    net_fee = fee - max(0, external_cost)
    l1 = round(net_fee * l1_rate) if l1_handle else 0
    l2 = round(net_fee * l2_rate) if l2_handle else 0
    lp_keep = fee - l1 - l2
    if lp_keep < 0:
        raise ValueError(f"affiliate split ({l1}+{l2}) exceeds fee ({fee})")
    entries = [
        Entry(SYS_ESCROW, -amount, "release"),
        Entry(seller_handle, amount - fee, "release"),
        Entry(SYS_LP_FEES, lp_keep, "platform_fee"),
    ]
    if l1_handle and l1:
        entries.append(Entry(l1_handle, l1, "affiliate_l1"))
    if l2_handle and l2:
        entries.append(Entry(l2_handle, l2, "affiliate_l2"))
    return _assert_balanced(entries)


def plan_refund(buyer_handle: str, credits: int):
    """Refund held escrow back to the buyer if a Hire is cancelled before delivery."""
    if credits <= 0:
        raise ValueError("refund must be positive")
    return _assert_balanced([
        Entry(SYS_ESCROW, -credits, "refund"),
        Entry(buyer_handle, credits, "refund"),
    ])


def plan_payout(seller_handle: str, credits: int):
    """Cash out: burn `credits` from the seller (the real money leaves via Stripe
    Connect, off-ledger); the issued/liability wallet rises as credits exit circulation."""
    if credits <= 0:
        raise ValueError("payout credits must be positive")
    return _assert_balanced([
        Entry(seller_handle, -credits, "payout"),
        Entry(SYS_ISSUED, credits, "payout"),
    ])


# ── Persistence (atomic; one transaction per post) ───────────────────────────

async def _wallet_id(conn, handle: str) -> int:
    r = await conn.execute("SELECT id FROM wallets WHERE owner_handle = %s", (handle,))
    row = await r.fetchone()
    if row:
        return row["id"]
    kind = "system" if handle.startswith("_") else "member"
    r = await conn.execute(
        "INSERT INTO wallets (owner_handle, kind) VALUES (%s, %s) RETURNING id",
        (handle, kind))
    return (await r.fetchone())["id"]


async def balance(handle: str) -> int:
    from src.memory.database import get_db
    async with get_db() as conn:
        r = await conn.execute("SELECT balance FROM wallets WHERE owner_handle = %s", (handle,))
        row = await r.fetchone()
        return row["balance"] if row else 0


async def post(txn_type: str, entries, *, idempotency_key: str = None,
               source_handle: str = None, related_handle: str = None,
               listing_id: str = None, gross: int = 0, external_ref: str = None,
               allow_negative: bool = False, metadata: dict = None) -> dict:
    """Persist a balanced entry set atomically. psycopg's `async with` commits the whole
    block on success / rolls back on error, so the txn + all entries + balance updates
    are one transaction. An existing idempotency_key short-circuits (webhook-retry safe).
    Member wallets cannot go negative (insufficient-funds guard); system wallets may."""
    from src.memory.database import get_db
    _assert_balanced(entries)
    async with get_db() as conn:
        if idempotency_key:
            r = await conn.execute("SELECT id FROM txns WHERE idempotency_key = %s", (idempotency_key,))
            existing = await r.fetchone()
            if existing:
                return {"txn_id": existing["id"], "idempotent_replay": True}

        txn_id = str(uuid.uuid4())
        await conn.execute(
            "INSERT INTO txns (id, type, source_handle, related_handle, listing_id, "
            "gross, external_ref, idempotency_key, metadata) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (txn_id, txn_type, source_handle, related_handle, listing_id, gross,
             external_ref, idempotency_key, json.dumps(metadata or {})))

        for e in entries:
            wid = await _wallet_id(conn, e.handle)
            r = await conn.execute("SELECT balance, kind FROM wallets WHERE id = %s FOR UPDATE", (wid,))
            row = await r.fetchone()
            new_bal = row["balance"] + e.delta
            if new_bal < 0 and not allow_negative and row["kind"] != "system":
                raise ValueError(
                    f"insufficient balance for {e.handle}: {row['balance']} {e.delta:+d}")
            await conn.execute(
                "INSERT INTO ledger_entries (txn_id, wallet_id, delta, kind) VALUES (%s, %s, %s, %s)",
                (txn_id, wid, e.delta, e.kind))
            await conn.execute(
                "UPDATE wallets SET balance = %s, updated_at = NOW() WHERE id = %s", (new_bal, wid))

        return {"txn_id": txn_id, "idempotent_replay": False}
