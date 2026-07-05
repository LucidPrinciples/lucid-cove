-- 013b: Fix Atlas echoes stored under wrong agent_id ("atlas-cove" → "atlas")
-- Assigns sequential echo_nums starting after the current max for "atlas"

-- First, let's see what we're working with
SELECT 'atlas-cove echoes:' as info, id, echo_num, frequency, tuned_at
FROM echoes WHERE agent_id = 'atlas-cove' ORDER BY tuned_at;

SELECT 'atlas max echo_num:' as info, MAX(echo_num) as max_num
FROM echoes WHERE agent_id = 'atlas';

-- Reassign with sequential numbering using a CTE
WITH ranked AS (
    SELECT id,
           ROW_NUMBER() OVER (ORDER BY tuned_at) as rn
    FROM echoes
    WHERE agent_id = 'atlas-cove'
),
base AS (
    SELECT COALESCE(MAX(echo_num), 0) as max_num
    FROM echoes WHERE agent_id = 'atlas'
)
UPDATE echoes e
SET agent_id = 'atlas',
    echo_num = base.max_num + ranked.rn
FROM ranked, base
WHERE e.id = ranked.id;

-- Fix process_records echo_nums to match (they were already reassigned to 'atlas')
-- Need to update their echo_num values to match the new echo numbers
-- Actually, process_records reference echo_num — let's check what's there
SELECT 'process_records under atlas:' as info, id, agent_id, echo_num, created_at
FROM process_records WHERE agent_id = 'atlas' ORDER BY created_at DESC LIMIT 10;
