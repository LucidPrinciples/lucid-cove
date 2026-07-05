-- 025: CF-1 — presence isolation for the social/youtube queues (strict
-- self-scope, operator decision 2026-07-04). Every UI surface that lists or
-- edits youtube_queue / social_queue rows shows ONLY the acting presence's
-- rows — no admin override, no master view. Background processors (scheduled
-- upload/posting jobs) are Cove machinery and stay unscoped. This migration
-- adds the presence_id column those UI queries scope on.
--
-- Backfill rationale: existing rows predate scoping. They were all created
-- through the founding admin's flows, so they belong to the oldest active
-- admin account. The backfill is guarded so it no-ops on databases where the
-- accounts table doesn't exist (single-Cove installs may never create it;
-- single mode applies no scoping anyway).
-- Idempotent; safe to re-run.

ALTER TABLE youtube_queue ADD COLUMN IF NOT EXISTS presence_id TEXT;
ALTER TABLE social_queue  ADD COLUMN IF NOT EXISTS presence_id TEXT;

CREATE INDEX IF NOT EXISTS idx_youtube_queue_presence_id ON youtube_queue (presence_id);
CREATE INDEX IF NOT EXISTS idx_social_queue_presence_id  ON social_queue (presence_id);

DO $$
BEGIN
    IF to_regclass('public.accounts') IS NOT NULL THEN
        UPDATE youtube_queue
           SET presence_id = (SELECT id::text FROM accounts
                              WHERE cove_role = 'admin' AND active = TRUE
                              ORDER BY created_at LIMIT 1)
         WHERE presence_id IS NULL;
        UPDATE social_queue
           SET presence_id = (SELECT id::text FROM accounts
                              WHERE cove_role = 'admin' AND active = TRUE
                              ORDER BY created_at LIMIT 1)
         WHERE presence_id IS NULL;
    END IF;
END $$;
