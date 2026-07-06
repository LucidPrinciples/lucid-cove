-- 027 — Haven steward identity (steward-owned Haven Space, §2 2026-07-06).
-- Adds durable Haven-steward Matrix creds to cove_haven so the Haven Space + Commons
-- survive operator churn — the Haven-level mirror of cove_matrix's steward columns.
-- Idempotent: safe to re-run. CREATE guards a DB that predates cove_haven; the ALTERs
-- add the columns to any existing cove_haven that lacks them.
CREATE TABLE IF NOT EXISTS cove_haven (
    haven_id       TEXT PRIMARY KEY,
    name           TEXT,
    owner_user     TEXT,
    space_id       TEXT,
    commons_id     TEXT,
    steward_username TEXT,
    steward_password TEXT,
    created_at     TIMESTAMPTZ DEFAULT NOW(),
    updated_at     TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE cove_haven ADD COLUMN IF NOT EXISTS steward_username TEXT;
ALTER TABLE cove_haven ADD COLUMN IF NOT EXISTS steward_password TEXT;
