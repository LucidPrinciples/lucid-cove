--- Migration 014: Add timezone column to accounts table.
--- Per-Presence timezone override. Falls back to Cove-level timezone
--- (from cove.yaml) if NULL. IANA timezone string (e.g. "America/New_York").
--- Safe to run multiple times.

DO $$ BEGIN
    ALTER TABLE accounts ADD COLUMN timezone VARCHAR(50);
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;
