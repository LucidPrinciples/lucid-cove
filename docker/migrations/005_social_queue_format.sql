-- Migration 005: Add format column to social_queue.
--
-- is_vertical (boolean) can't distinguish horizontal from square.
-- Add a text column for the actual format name. Backfill existing rows:
-- is_vertical=true → 'vertical', is_vertical=false → 'horizontal' (best guess).
-- New inserts will set format explicitly.

ALTER TABLE social_queue
    ADD COLUMN IF NOT EXISTS format TEXT NOT NULL DEFAULT 'vertical'
    CHECK (format IN ('vertical', 'horizontal', 'square'));

-- Backfill from is_vertical
UPDATE social_queue SET format = 'horizontal' WHERE NOT is_vertical;
