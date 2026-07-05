-- Migration 010: Nextcloud credentials for Operator+ tier
-- Adds per-user Nextcloud username and app password to accounts table.
-- Used by the shared container to route WebDAV/CalDAV to per-user NC accounts.
-- Safe to run on any container (IF NOT EXISTS guards).

-- Per-user Nextcloud credentials
DO $$ BEGIN
    ALTER TABLE accounts ADD COLUMN nc_username VARCHAR(100);
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE accounts ADD COLUMN nc_password TEXT;
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;
