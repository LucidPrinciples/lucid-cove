-- =============================================================================
-- add-presence-profiles.sql (#169) — public Presence presentation (hub).
-- Keyed by registry @handle; extends accounts identity with avatars/bio/skills for
-- the searchable marketplace. Idempotent. Apply: RB12 on the hub (lucidcove_shared).
-- Spec: LP-Vault/Reference/commerce-credit-economy-spec.md §8.
-- =============================================================================
CREATE TABLE IF NOT EXISTS presence_profiles (
    handle            TEXT PRIMARY KEY,
    avatar_url        TEXT,
    agent_avatar_url  TEXT,
    bio               TEXT,
    skills            JSONB DEFAULT '[]'::jsonb,
    links             JSONB DEFAULT '{}'::jsonb,
    updated_at        TIMESTAMPTZ DEFAULT NOW()
);
