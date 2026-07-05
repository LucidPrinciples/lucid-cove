-- 015_agent_identity.sql
-- Centralized model (COVE_MODE=multi): a Presence's personal agent is a DATA ENTRY,
-- not a container with an agent.yaml file. This column is the per-Presence agent
-- identity derived by the archetype discovery flow — the Centralized analog of the
-- agent.yaml entry the Isolated path writes to disk.
--
-- Shape (JSONB): {
--   agent_name, archetype, archetype_desc, frequency, frequency_color,
--   frequency_essence, tuning_key, tuning_key_song, pronouns, gender,
--   qualities[], feeling, persona, first_message, role, channels[], provisioned_at
-- }
--
-- Written by POST /api/presence/provision (src/dashboard/routes/presence.py).
-- Kept as a single JSONB column (not spread across dedicated columns) so the
-- identity schema can evolve without forcing a migration on every self-hosted
-- Cove, and so a Presence's identity exports as one clean bundle (portability).
--
-- Idempotent. Safe to run on any existing Cove (Clearfield, Stuart, Atlas, shared).

ALTER TABLE accounts
    ADD COLUMN IF NOT EXISTS agent_identity JSONB DEFAULT '{}';
