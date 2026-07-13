-- 035: tuner_cycle — training examples and export tracking for the watcher (#D45).
--
-- Tracks NEW training examples since last export and when the last tuner cycle ran.
-- The watcher check `tuner-cycle-due` fires when either:
--   1. NEW training examples since last export >= threshold (default 500)
--   2. OR last cycle was > 30 days ago

-- Training examples — incremental data points collected for model tuning
CREATE TABLE IF NOT EXISTS training_examples (
    id          SERIAL PRIMARY KEY,
    content     TEXT NOT NULL,           -- the actual training data
    source      TEXT DEFAULT '',         -- where it came from (conversation, tuning, etc.)
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_training_examples_created_at
    ON training_examples (created_at DESC);

-- Tuner export cycles — records of each training data export
CREATE TABLE IF NOT EXISTS tuner_exports (
    id              SERIAL PRIMARY KEY,
    example_count   INTEGER NOT NULL,    -- how many examples were exported
    exported_at     TIMESTAMPTZ DEFAULT NOW(),
    notes           TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_tuner_exports_exported_at
    ON tuner_exports (exported_at DESC);
