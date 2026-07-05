-- Per-operator Matrix identity for Connect SSO (#137).
-- Mirrors the nc_username/nc_password pattern. Used by /api/matrix/token in multi mode.
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS matrix_username TEXT;
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS matrix_password TEXT;
