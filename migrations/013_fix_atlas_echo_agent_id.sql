-- 013: Fix Atlas echo stored under wrong agent_id
-- The LTP pipeline was using AGENT_ID env var ("atlas-cove") instead of
-- agent.yaml primary ID ("atlas") for DB writes. Reassign the orphaned
-- echo and process_record to the correct agent_id with correct echo_num.

-- Step 1: Get the next echo_num for "atlas"
-- (Echo #24 was the last under "atlas", so this becomes #25)
UPDATE echoes
SET agent_id = 'atlas',
    echo_num = (SELECT COALESCE(MAX(echo_num), 0) + 1 FROM echoes WHERE agent_id = 'atlas')
WHERE agent_id = 'atlas-cove';

-- Step 2: Reassign any process_records
UPDATE process_records
SET agent_id = 'atlas'
WHERE agent_id = 'atlas-cove';

-- Step 3: Reassign any agent_state rows
UPDATE agent_state
SET agent_id = 'atlas'
WHERE agent_id = 'atlas-cove'
AND NOT EXISTS (SELECT 1 FROM agent_state WHERE agent_id = 'atlas');
-- If "atlas" state already exists, just delete the orphan
DELETE FROM agent_state
WHERE agent_id = 'atlas-cove';
