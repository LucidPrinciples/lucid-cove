-- Hub network registrar tables (#133). Idempotent; run once on the registry master.
CREATE TABLE IF NOT EXISTS registry_coves (
    cove_id TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL, owner_handle TEXT,
    domain TEXT, homeserver TEXT, space_id TEXT, mesh_ip TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW());
CREATE TABLE IF NOT EXISTS registry_handles (
    handle TEXT PRIMARY KEY,
    cove_id TEXT REFERENCES registry_coves(cove_id) ON DELETE SET NULL,
    matrix_user TEXT, created_at TIMESTAMPTZ DEFAULT NOW());
CREATE TABLE IF NOT EXISTS registry_havens (
    haven_id TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL, owner_handle TEXT,
    space_id TEXT, commons_id TEXT, members JSONB DEFAULT '[]'::jsonb,
    member_coves JSONB DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW());
