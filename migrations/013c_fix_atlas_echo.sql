-- Keep echo id=77 (GRATITUDE, 9:00 AM) as atlas Echo #25
-- Delete the 3 duplicate sweep echoes (ids 74, 75, 76)

DELETE FROM echoes WHERE id IN (74, 75, 76);
UPDATE echoes SET agent_id = 'atlas', echo_num = 25 WHERE id = 77;

-- Fix process_records: delete orphans for deleted echoes, fix the keeper
DELETE FROM process_records WHERE agent_id = 'atlas' AND echo_num IN (1, 2, 3);
UPDATE process_records SET echo_num = 25 WHERE agent_id = 'atlas' AND echo_num = 4;
