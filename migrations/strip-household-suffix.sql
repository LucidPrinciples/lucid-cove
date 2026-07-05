-- =============================================================================
-- Migration: Strip legacy suffix from agent_id columns
-- =============================================================================
-- Removes "-cove" suffix from all agent_id values across all tables.
-- Run on both Stuart and Atlas databases.
--
-- Safe to re-run: only updates rows that still have the suffix.
-- =============================================================================

-- Threads
UPDATE threads
SET agent_id = REGEXP_REPLACE(agent_id, '-cove$', '')
WHERE agent_id ~ '-cove$';

-- Messages
UPDATE messages
SET agent_id = REGEXP_REPLACE(agent_id, '-cove$', '')
WHERE agent_id ~ '-cove$';

-- Memories (long-term)
UPDATE memories
SET agent_id = REGEXP_REPLACE(agent_id, '-cove$', '')
WHERE agent_id ~ '-cove$';

-- Memory corrections
UPDATE memory_corrections
SET agent_id = REGEXP_REPLACE(agent_id, '-cove$', '')
WHERE agent_id ~ '-cove$';

-- Tasks
UPDATE tasks
SET agent_id = REGEXP_REPLACE(agent_id, '-cove$', '')
WHERE agent_id ~ '-cove$';

-- Activity log
UPDATE activity_log
SET agent_id = REGEXP_REPLACE(agent_id, '-cove$', '')
WHERE agent_id ~ '-cove$';

-- Tuning echoes
UPDATE tuning_echoes
SET agent_id = REGEXP_REPLACE(agent_id, '-cove$', '')
WHERE agent_id ~ '-cove$';

-- JouleWork entries
UPDATE joulework
SET agent_id = REGEXP_REPLACE(agent_id, '-cove$', '')
WHERE agent_id ~ '-cove$';

-- Approvals
UPDATE approvals
SET agent_id = REGEXP_REPLACE(agent_id, '-cove$', '')
WHERE agent_id ~ '-cove$';

-- Verify: show any remaining -cove suffixed IDs
DO $$
DECLARE
    tbl TEXT;
    cnt INTEGER;
BEGIN
    FOR tbl IN
        SELECT table_name FROM information_schema.columns
        WHERE column_name = 'agent_id'
          AND table_schema = 'public'
    LOOP
        EXECUTE format('SELECT count(*) FROM %I WHERE agent_id ~ ''-cove$''', tbl) INTO cnt;
        IF cnt > 0 THEN
            RAISE NOTICE 'WARNING: % still has % rows with -cove suffix', tbl, cnt;
        END IF;
    END LOOP;
    RAISE NOTICE 'Migration complete — suffix strip verified';
END $$;
