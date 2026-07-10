-- 032: #D25 (a) — backfill a STABLE presence identity onto manager chat_threads.
--
-- The supervisory chat view groups threads by presence. Legacy/migration-era manager
-- threads were written without metadata.presence_id, so one person fragmented into ghost
-- tabs ("Presence 6119", "Agent") and the live thread could hide under a ghost key.
--
-- This backfill is ADDITIVE and IDEMPOTENT. It only TAGS metadata (presence_id, and
-- operator_name when missing) on threads whose agent_id resolves to a CURRENT ACTIVE
-- account — manager threads are scoped by the presence account UUID, so agent_id already
-- IS that stable id for them. It NEVER deletes a row, NEVER merges-destroys, and NEVER
-- touches a thread that doesn't resolve to a current account: those are quarantined as
-- "Orphaned" at read time (routes/memory.py), which needs no data change. Safe to re-run
-- (the WHERE excludes rows already tagged).
--
-- Deliberately conservative: the ambiguous operator_name -> account merge (dup display
-- names) is resolved live in the read path, not here, so this migration can never
-- mis-attribute one person's conversation to another.

UPDATE chat_threads t
SET metadata = COALESCE(t.metadata, '{}'::jsonb)
    || jsonb_build_object('presence_id', a.id::text)
    || CASE
         WHEN COALESCE(t.metadata->>'operator_name', '') = ''
           THEN jsonb_build_object('operator_name', a.display_name)
         ELSE '{}'::jsonb
       END
FROM accounts a
WHERE a.active = TRUE
  AND a.id::text = t.agent_id
  AND COALESCE(t.metadata->>'presence_id', '') = '';
