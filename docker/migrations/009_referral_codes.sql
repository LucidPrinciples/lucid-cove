-- =============================================================================
-- Migration 009: Add referral_code column to accounts table
-- =============================================================================
-- Every account gets a permanent referral code (e.g., LP4829) assigned at
-- creation. This replaces username-based referral links. The code persists
-- across tier upgrades, Presence naming, and Cove migration.
--
-- Format: "LP" + 4-digit zero-padded number (LP0001 through LP9999).
-- For scale beyond 9999, extend to 5+ digits automatically.
-- Safe to run multiple times.
-- =============================================================================

-- Add the column if it doesn't exist
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'accounts' AND column_name = 'referral_code'
    ) THEN
        ALTER TABLE accounts ADD COLUMN referral_code TEXT;
    END IF;
END $$;

-- Generate codes for existing accounts that don't have one
-- Uses a sequence-like approach: row_number gives deterministic ordering by created_at
DO $$
DECLARE
    r RECORD;
    code_num INTEGER := 1000;  -- Start at LP1000 for existing users
BEGIN
    FOR r IN
        SELECT id FROM accounts
        WHERE referral_code IS NULL
        ORDER BY created_at ASC
    LOOP
        code_num := code_num + 1;
        UPDATE accounts SET referral_code = 'LP' || code_num WHERE id = r.id;
    END LOOP;
END $$;

-- Add unique index
CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_referral_code
    ON accounts(referral_code) WHERE referral_code IS NOT NULL;
