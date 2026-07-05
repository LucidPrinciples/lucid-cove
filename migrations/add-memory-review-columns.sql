-- Add memory review columns for contradiction detection
-- Run on both Stuart and Atlas databases

ALTER TABLE agent_memory ADD COLUMN IF NOT EXISTS needs_review BOOLEAN DEFAULT FALSE;
ALTER TABLE agent_memory ADD COLUMN IF NOT EXISTS review_reason TEXT;

CREATE INDEX IF NOT EXISTS idx_memory_needs_review
    ON agent_memory(agent_id) WHERE needs_review = TRUE AND is_active = TRUE;
