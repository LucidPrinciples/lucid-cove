-- 050: Admin deactivate metadata for hub accounts (HAVENMC2 S1).
-- Additive only. accounts.active already gates sign-in and public lists.

ALTER TABLE accounts
    ADD COLUMN IF NOT EXISTS deactivated_at TIMESTAMPTZ;

ALTER TABLE accounts
    ADD COLUMN IF NOT EXISTS deactivated_by TEXT;

ALTER TABLE accounts
    ADD COLUMN IF NOT EXISTS deactivated_reason TEXT;
