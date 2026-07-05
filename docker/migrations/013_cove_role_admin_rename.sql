-- 013_cove_role_admin_rename.sql
-- Rename the in-Cove admin role VALUE: cove_role 'operator' -> 'admin'.
-- "operator" collided with the human/Operator-tier term; the locked vocabulary is
-- Admin Presence vs Member Presence (init-base.sql already documents 'admin'|'member'|'guest').
-- Idempotent: re-running is a no-op once values are 'admin'. Auto-applied by the
-- entrypoint migration runner (#171) on every cove-core container start.
UPDATE accounts SET cove_role = 'admin' WHERE cove_role = 'operator';
