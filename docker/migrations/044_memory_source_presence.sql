-- 044: Steward memory provenance — which presence/human the turn belonged to.
-- Manager memory stays one shared pool; these columns label who was on the
-- Mission Control session when the row was written. Idempotent.

ALTER TABLE agent_memory ADD COLUMN IF NOT EXISTS source_presence_id UUID;
ALTER TABLE agent_memory ADD COLUMN IF NOT EXISTS source_operator_name TEXT;

CREATE INDEX IF NOT EXISTS idx_memory_source_presence
    ON agent_memory (source_presence_id)
    WHERE source_presence_id IS NOT NULL AND is_active = TRUE;
