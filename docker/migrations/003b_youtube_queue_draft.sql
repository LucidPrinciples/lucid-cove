-- Migration 003: Add 'draft' status to youtube_queue
-- Draft = known content not yet scheduled. Actions tab shows these as individual items.

-- Add 'draft' to the status CHECK constraint
ALTER TABLE youtube_queue DROP CONSTRAINT IF EXISTS youtube_queue_status_check;
ALTER TABLE youtube_queue ADD CONSTRAINT youtube_queue_status_check
    CHECK (status IN ('draft','queued','uploading','uploaded','published','failed','cancelled'));

-- NOTE (#206, open-source): this migration previously seeded 6 founder YouTube
-- shorts (the "How Lucid Tuner Was Built" series) into every Cove's queue. That
-- founder content has been removed so a clean install ships with an empty queue.
-- A Cove's own content arrives via the Actions tab, not a migration seed.
