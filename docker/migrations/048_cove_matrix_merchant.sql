-- 048: merchant Matrix identity + its own Family-room /sync cursor.
-- Steward keeps owning the Space. Mercer is a second manager in the
-- same Family room and must not share the steward sync cursor.
-- Idempotent.

ALTER TABLE cove_matrix ADD COLUMN IF NOT EXISTS merchant_username TEXT;
ALTER TABLE cove_matrix ADD COLUMN IF NOT EXISTS merchant_password TEXT;
ALTER TABLE cove_matrix ADD COLUMN IF NOT EXISTS merchant_sync_next_batch TEXT;
