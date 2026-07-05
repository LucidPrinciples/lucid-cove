-- Connect/Matrix Space ownership table for an existing Cove (#137 Phase A).
-- Idempotent. Run once per existing Cove DB; fresh Coves get it from init-base.sql.
CREATE TABLE IF NOT EXISTS cove_matrix (
    id               INTEGER PRIMARY KEY DEFAULT 1,
    steward_username TEXT,
    steward_password TEXT,
    space_id         TEXT,
    family_room_id   TEXT,
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    updated_at       TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT cove_matrix_singleton CHECK (id = 1)
);
