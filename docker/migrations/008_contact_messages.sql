-- =============================================================================
-- Migration 008: Contact messages table
-- =============================================================================
-- Simple user feedback / question system. Messages submitted from the help
-- overlay in any tier. Operator views and archives them from Haven MC.
-- =============================================================================

CREATE TABLE IF NOT EXISTS contact_messages (
    id              SERIAL PRIMARY KEY,
    account_id      UUID REFERENCES accounts(id),
    email           TEXT NOT NULL,
    display_name    TEXT,
    username        TEXT,
    tier            TEXT,
    subject         TEXT DEFAULT '',
    message         TEXT NOT NULL,
    archived        BOOLEAN DEFAULT FALSE,
    archived_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_contact_messages_archived
    ON contact_messages(archived, created_at DESC);
