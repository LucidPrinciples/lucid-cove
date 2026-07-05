-- Migration: Add coaching_text column to echoes table
-- Stores LT's coaching prompt that the agent received before their tuning practice.
-- The echo_text is the agent's reflection (output); coaching_text is the input.

ALTER TABLE echoes ADD COLUMN IF NOT EXISTS coaching_text TEXT;

-- Backfill note: existing echoes won't have coaching_text.
-- That's fine — it only populates going forward from the next tuning.
