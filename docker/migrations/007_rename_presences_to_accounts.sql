-- =============================================================================
-- Migration 007: Rename presences → accounts, add preferences column
-- =============================================================================
-- The shared VPS container serves Free and Operator tiers — not Presences.
-- "accounts" is the correct term for users on the shared container.
-- Presences (with agents) get their own dedicated containers.
--
-- FK columns (presence_id) in other tables are left as-is — PostgreSQL
-- automatically follows the table rename for constraint references.
-- Safe to run multiple times.
-- =============================================================================

-- Rename table (only if old name still exists)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'presences') THEN
        ALTER TABLE presences RENAME TO accounts;
    END IF;
END $$;

-- Rename indexes to match new table name
ALTER INDEX IF EXISTS idx_presences_username RENAME TO idx_accounts_username;
ALTER INDEX IF EXISTS idx_presences_email RENAME TO idx_accounts_email;
ALTER INDEX IF EXISTS idx_presences_auth_token RENAME TO idx_accounts_auth_token;
ALTER INDEX IF EXISTS idx_presences_cove_id RENAME TO idx_accounts_cove_id;
ALTER INDEX IF EXISTS idx_presences_tier RENAME TO idx_accounts_tier;
ALTER INDEX IF EXISTS idx_presences_referred_by RENAME TO idx_accounts_referred_by;

-- Add preferences column for per-account feature flags
-- (shared container config is read-only, so feature toggles go in DB)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'accounts' AND column_name = 'preferences'
    ) THEN
        ALTER TABLE accounts ADD COLUMN preferences JSONB DEFAULT '{}';
    END IF;
END $$;
