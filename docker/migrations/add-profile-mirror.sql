-- =============================================================================
-- add-profile-mirror.sql (#173) — the hub-side cross-Cove profile mirror.
-- The hub has no cross-Cove identity (registry_handles carries only handle/cove/
-- matrix/referred_by; accounts + presence_profiles are per-instance). This table is
-- the hub's resolvable copy of every Presence's PUBLIC profile, keyed by @handle, so
-- a seller on Cove B is viewable + searchable from Cove A (and the hub). Each instance
-- pushes its presences here (best-effort) on profile save / avatar / first listing.
-- Public presentation only — no private data. Idempotent. Apply: RB12 on the hub
-- (lucidcove_shared); auto-applied by the entrypoint migration sweep (#171).
-- Spec: LP-Vault/Reference/commerce-credit-economy-spec.md §8.
-- =============================================================================
CREATE TABLE IF NOT EXISTS profile_mirror (
    handle            TEXT PRIMARY KEY,
    display_name      TEXT,
    agent_name        TEXT,
    cove              TEXT,
    archetype         TEXT,
    frequency         TEXT,
    tuning_key        TEXT,
    nickname          TEXT,
    avatar_url        TEXT,
    agent_avatar_url  TEXT,
    bio               TEXT,
    skills            JSONB DEFAULT '[]'::jsonb,
    links             JSONB DEFAULT '{}'::jsonb,
    updated_at        TIMESTAMPTZ DEFAULT NOW()
);
