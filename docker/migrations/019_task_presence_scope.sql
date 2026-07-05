-- 019: Agent-gate (#191) — operator-scope tasks + event_links on the public app.
--
-- tasks and event_links had no presence_id, so on the multi-tenant shared app
-- (LP_REGISTRY_MASTER) their endpoints could not be scoped per operator — any
-- operator could read/modify another's tasks and calendar links by id. Add the
-- column and backfill each task from its project's owner. NULL = single-mode
-- Cove (network-trusted family instance) — behavior unchanged there. Idempotent.

ALTER TABLE tasks ADD COLUMN IF NOT EXISTS presence_id UUID;

UPDATE tasks t
   SET presence_id = p.presence_id
  FROM projects p
 WHERE t.project_id = p.id
   AND t.presence_id IS NULL
   AND p.presence_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_tasks_presence ON tasks(presence_id);

ALTER TABLE event_links ADD COLUMN IF NOT EXISTS presence_id UUID;
CREATE INDEX IF NOT EXISTS idx_event_links_presence ON event_links(presence_id);
