-- 021: DB-backed per-agent model assignment (Team-page model manager).
--
-- Per-agent model assignment used to live only in YAML (agent.yaml model_primary /
-- cove.yaml team_models). That can't be UI-driven: /app/config is mounted read-only on
-- Coves, and YAML edits need a restart. Presences already store their model in the DB
-- (accounts.agent_identity.model) and save instantly from the Your-Agent UI. This table
-- gives team agents (Stuart, Mercer, specialists) the same: DB-backed, UI-driven, no restart.
--
-- Two axes: WORKING/chat model vs TUNING model (the cont.40 tuning-slot split). NULL on
-- any field = inherit from the existing YAML cascade (so an empty table = today's behavior).
-- Resolution adds this as the TOP-priority layer, served from a boot-loaded cache.
-- Idempotent.

CREATE TABLE IF NOT EXISTS agent_model_assignments (
    agent_id          TEXT PRIMARY KEY,
    working_primary   TEXT,
    working_fallback  TEXT,
    tuning_primary    TEXT,
    tuning_fallback   TEXT,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
