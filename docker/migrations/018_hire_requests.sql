-- Migration 018: Hire layer (#169) — the marketplace's services/labor half.
-- A Hire is pay-now-deliver-later: credits HELD in escrow at request, RELEASED
-- to the seller on delivery (fee + affiliate, same split as a sale) or REFUNDED
-- on cancel. Seller may be a human OR an agent handle. Lives with the ledger
-- (hub). Idempotent. Auto-applied by the entrypoint (#171).

CREATE TABLE IF NOT EXISTS hire_requests (
    id             BIGSERIAL PRIMARY KEY,
    buyer_handle   TEXT NOT NULL,
    seller_handle  TEXT NOT NULL,
    title          TEXT NOT NULL DEFAULT '',
    listing_ref    TEXT,
    amount_credits BIGINT NOT NULL,
    state          TEXT NOT NULL DEFAULT 'requested',  -- requested|accepted|delivered|released|cancelled
    delivery_ref   TEXT,
    thread_id      TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_hire_buyer  ON hire_requests(buyer_handle, state);
CREATE INDEX IF NOT EXISTS idx_hire_seller ON hire_requests(seller_handle, state);
