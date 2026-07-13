-- 036: thread-lifecycle hardening (Fix A) — at most ONE active thread per
-- (agent_id, channel).
--
-- Duplicate/empty active threads accumulated because _get_active_thread_id +
-- create_thread were find-else-create with no lock and no DB constraint: a race,
-- a container restart, or an agent_id scope shift let two turns both INSERT an
-- active. This adds the DB-level guarantee the app layer (create_thread's
-- ON CONFLICT) infers against.
--
-- ADDITIVE + IDEMPOTENT. Never deletes a row (archives, so a later backfill can
-- still extract memories). Safe to re-run on founder + VPS, which may still hold
-- duplicates from before this landed.

-- 1. Collapse existing duplicate actives: keep the NEWEST per (agent_id, channel),
--    archive the rest. Empty ghosts just archive; real dups keep their history for
--    extraction. Must run BEFORE the unique index or index creation would fail.
WITH ranked AS (
    SELECT id,
           row_number() OVER (
               PARTITION BY agent_id, channel
               ORDER BY created_at DESC, id DESC
           ) AS rn
    FROM chat_threads
    WHERE status = 'active'
)
UPDATE chat_threads t
   SET status = 'archived',
       archived_at = COALESCE(t.archived_at, NOW())
  FROM ranked r
 WHERE t.id = r.id
   AND r.rn > 1;

-- 2. Enforce it going forward.
CREATE UNIQUE INDEX IF NOT EXISTS chat_threads_one_active
    ON chat_threads (agent_id, channel)
    WHERE status = 'active';
