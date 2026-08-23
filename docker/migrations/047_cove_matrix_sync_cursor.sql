-- 047: persist the Family-room Matrix sync cursor on the existing singleton.
-- The mention worker resumes after restart without replaying old messages.
-- Idempotent.

ALTER TABLE cove_matrix ADD COLUMN IF NOT EXISTS sync_next_batch TEXT;
