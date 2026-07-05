-- oauth_tokens — Store OAuth2 tokens for external services (YouTube, etc.)
-- One row per service. Tokens auto-refresh via the API layer.

CREATE TABLE IF NOT EXISTS oauth_tokens (
    service         TEXT PRIMARY KEY,           -- e.g. 'youtube'
    access_token    TEXT NOT NULL,
    refresh_token   TEXT,                       -- NULL if not granted
    expires_at      TIMESTAMPTZ,               -- When access_token expires
    scope           TEXT,                       -- Space-separated scopes
    token_type      TEXT DEFAULT 'Bearer',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);
