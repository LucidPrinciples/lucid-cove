-- Add review window columns to agent_memory
-- Memories start unreviewed. After 7 days or manual review, they auto-commit.

ALTER TABLE agent_memory ADD COLUMN IF NOT EXISTS reviewed BOOLEAN DEFAULT FALSE;
ALTER TABLE agent_memory ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ;

-- Mark all existing memories as already reviewed (they predate the review window)
UPDATE agent_memory SET reviewed = TRUE, reviewed_at = updated_at WHERE reviewed = FALSE;

-- Index for efficient review queue queries
CREATE INDEX IF NOT EXISTS idx_memory_review_queue
    ON agent_memory(agent_id, created_at DESC)
    WHERE is_active = TRUE AND reviewed = FALSE;
