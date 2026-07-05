-- =============================================================================
-- add-credit-ledger.sql (#128) — the internal credit ledger (hub-only).
-- Idempotent: safe to run on an existing DB; mirrors the block now in
-- init-base.sql so a fresh Cove gets it at init and an existing hub gets it here.
-- Spec: LP-Vault/Reference/commerce-credit-economy-spec.md §4.
-- Apply: RB12 (psql -f) on the hub (lucidcove_shared).
-- =============================================================================

CREATE TABLE IF NOT EXISTS wallets (
    id            SERIAL PRIMARY KEY,
    owner_handle  TEXT UNIQUE NOT NULL,
    kind          TEXT NOT NULL DEFAULT 'member',
    balance       BIGINT NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS txns (
    id              TEXT PRIMARY KEY,
    type            TEXT NOT NULL,
    source_handle   TEXT,
    related_handle  TEXT,
    listing_id      TEXT,
    gross           BIGINT NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'posted',
    external_ref    TEXT,
    idempotency_key TEXT UNIQUE,
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ledger_entries (
    id            BIGSERIAL PRIMARY KEY,
    txn_id        TEXT NOT NULL REFERENCES txns(id) ON DELETE RESTRICT,
    wallet_id     INTEGER NOT NULL REFERENCES wallets(id) ON DELETE RESTRICT,
    delta         BIGINT NOT NULL,
    kind          TEXT NOT NULL,
    ref_type      TEXT,
    ref_id        TEXT,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ledger_entries_wallet ON ledger_entries(wallet_id);
CREATE INDEX IF NOT EXISTS idx_ledger_entries_txn ON ledger_entries(txn_id);
