-- Haven Space ownership on this Cove (#160). Idempotent; run on an operator's Cove.
CREATE TABLE IF NOT EXISTS cove_haven (
    haven_id TEXT PRIMARY KEY, name TEXT, owner_user TEXT,
    space_id TEXT, commons_id TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW());
