-- 011_auth_sessions.sql — Multi-session auth support
-- Replaces single auth_token column on accounts with a sessions table.
-- Each magic link click creates a session. Multiple sessions can be active
-- simultaneously (phone + laptop + tablet). Sessions expire after 90 days.

CREATE TABLE IF NOT EXISTS auth_sessions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id      UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    token_hash      TEXT NOT NULL,
    device_label    TEXT,                          -- e.g. "Safari iPhone", "Chrome Laptop"
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '90 days'),
    active          BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS idx_auth_sessions_token
    ON auth_sessions(token_hash) WHERE active = TRUE;

CREATE INDEX IF NOT EXISTS idx_auth_sessions_account
    ON auth_sessions(account_id) WHERE active = TRUE;

-- Migrate existing tokens: create a session for each account's current auth_token
INSERT INTO auth_sessions (account_id, token_hash, device_label, created_at, last_used, expires_at)
SELECT id, auth_token, 'migrated', NOW(), NOW(), NOW() + INTERVAL '90 days'
FROM accounts
WHERE auth_token IS NOT NULL AND auth_token != ''
ON CONFLICT DO NOTHING;

-- Cap at 10 sessions per account (cleanup old ones)
-- This is enforced in code, not here.
