-- Migration 023: per-presence posting attribution on social_queue.
--
-- Posting accounts (X, YouTube) are per-Presence, not Cove-global. The queue
-- needs to record WHICH presence owns each card so the scheduler can post it
-- from that presence's own credentials (and the board can scope by presence).
--
-- agent_id holds the owning presence's account id (accounts.id). NULL rows are
-- legacy/global and fall back to env credentials, preserving prior behavior.

ALTER TABLE social_queue
    ADD COLUMN IF NOT EXISTS agent_id TEXT;

-- Find a presence's queued posts quickly (scheduler groups by owner).
CREATE INDEX IF NOT EXISTS idx_sq_agent
    ON social_queue (agent_id, platform, status);
