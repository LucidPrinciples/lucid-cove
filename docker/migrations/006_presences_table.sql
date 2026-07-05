-- =============================================================================
-- Migration 006: Create presences table with tier support
-- =============================================================================
-- Run via Runbook 12 on each database that needs multi-Presence support.
-- Safe to run multiple times (IF NOT EXISTS on everything).
--
-- For existing Coves (Stuart, Atlas): adds the table but COVE_MODE stays 'single'
--   until explicitly switched. No behavior change until COVE_MODE=multi.
-- For new Coves (Clearfield, VPS shared): table ready for account creation.
-- =============================================================================

CREATE TABLE IF NOT EXISTS presences (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Identity
    display_name    TEXT NOT NULL,
    username        TEXT,
    email           TEXT,
    agent_name      TEXT,
    last_name       TEXT DEFAULT '',
    -- Access control
    tier            TEXT NOT NULL DEFAULT 'presence',
    cove_role       TEXT DEFAULT 'member',
    cove_id         TEXT,
    -- Agent config
    agent_config    JSONB DEFAULT '{}',
    active_workflows TEXT[] DEFAULT '{}',
    api_mode        TEXT DEFAULT 'cove',
    -- Naming
    name_locked     BOOLEAN DEFAULT FALSE,
    -- Auth
    auth_token      TEXT NOT NULL,
    active          BOOLEAN DEFAULT TRUE,
    -- Billing
    stripe_customer_id TEXT,
    referred_by     UUID,
    -- Timestamps
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    last_access     TIMESTAMPTZ,
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Unique constraints
CREATE UNIQUE INDEX IF NOT EXISTS idx_presences_username
    ON presences(username) WHERE username IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_presences_email
    ON presences(email) WHERE email IS NOT NULL;

-- Lookup indexes
CREATE INDEX IF NOT EXISTS idx_presences_auth_token
    ON presences(auth_token) WHERE active = TRUE;
CREATE INDEX IF NOT EXISTS idx_presences_cove_id
    ON presences(cove_id);
CREATE INDEX IF NOT EXISTS idx_presences_tier
    ON presences(tier);
CREATE INDEX IF NOT EXISTS idx_presences_referred_by
    ON presences(referred_by);
