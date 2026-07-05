-- =============================================================================
-- Migration: Strip "-cove" suffix from agent identifiers
-- =============================================================================
-- Built from actual schema in cove-core/docker/init-base.sql
-- and agent-specific init.sql files.
--
-- Tables from init-base.sql with agent_id column:
--   echoes, agent_state, process_records, jw_metrics, agent_memory, chat_threads
--
-- Tables from agent init.sql files:
--   tasks (uses "assignee" column, not agent_id)
--
-- Safe to re-run: only updates rows that still have the suffix.
-- =============================================================================

-- ─── Base schema tables (from init-base.sql) ────────────────────────────────

UPDATE echoes
SET agent_id = REGEXP_REPLACE(agent_id, '-cove$', '')
WHERE agent_id ~ '-cove$';

-- agent_state has agent_id as PRIMARY KEY — need careful handling
-- Update non-conflicting rows first
UPDATE agent_state
SET agent_id = REGEXP_REPLACE(agent_id, '-cove$', '')
WHERE agent_id ~ '-cove$'
  AND REGEXP_REPLACE(agent_id, '-cove$', '') NOT IN (SELECT agent_id FROM agent_state);

-- If a first-name row already exists alongside a -cove row, delete the -cove duplicate
DELETE FROM agent_state
WHERE agent_id ~ '-cove$'
  AND REGEXP_REPLACE(agent_id, '-cove$', '') IN (SELECT agent_id FROM agent_state);

UPDATE process_records
SET agent_id = REGEXP_REPLACE(agent_id, '-cove$', '')
WHERE agent_id ~ '-cove$';

UPDATE jw_metrics
SET agent_id = REGEXP_REPLACE(agent_id, '-cove$', '')
WHERE agent_id ~ '-cove$';

UPDATE agent_memory
SET agent_id = REGEXP_REPLACE(agent_id, '-cove$', '')
WHERE agent_id ~ '-cove$';

UPDATE chat_threads
SET agent_id = REGEXP_REPLACE(agent_id, '-cove$', '')
WHERE agent_id ~ '-cove$';

-- ─── Agent-specific tables ──────────────────────────────────────────────────

-- tasks.assignee (Stuart and Atlas both have this column)
UPDATE tasks
SET assignee = REGEXP_REPLACE(assignee, '-cove$', '')
WHERE assignee ~ '-cove$';

-- Also update display_name in agent_state (strip " Cove" surname)
UPDATE agent_state
SET display_name = REGEXP_REPLACE(display_name, ' Cove$', '')
WHERE display_name ~ ' Cove$';

-- ─── Verify ─────────────────────────────────────────────────────────────────

DO $$
DECLARE
    tbl TEXT;
    col TEXT;
    cnt INTEGER;
BEGIN
    -- Check all agent_id columns
    FOR tbl IN
        SELECT table_name FROM information_schema.columns
        WHERE column_name = 'agent_id' AND table_schema = 'public'
    LOOP
        EXECUTE format('SELECT count(*) FROM %I WHERE agent_id ~ ''-cove$''', tbl) INTO cnt;
        IF cnt > 0 THEN
            RAISE NOTICE 'REMAINING: %.agent_id has % rows with -cove', tbl, cnt;
        END IF;
    END LOOP;

    -- Check tasks.assignee
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'tasks' AND column_name = 'assignee' AND table_schema = 'public') THEN
        SELECT count(*) INTO cnt FROM tasks WHERE assignee ~ '-cove$';
        IF cnt > 0 THEN
            RAISE NOTICE 'REMAINING: tasks.assignee has % rows with -cove', cnt;
        END IF;
    END IF;

    -- Check agent_state.display_name
    SELECT count(*) INTO cnt FROM agent_state WHERE display_name ~ ' Cove$';
    IF cnt > 0 THEN
        RAISE NOTICE 'REMAINING: agent_state.display_name has % rows with Cove surname', cnt;
    END IF;

    RAISE NOTICE 'Migration complete — verified';
END $$;
