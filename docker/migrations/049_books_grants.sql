-- 049: Human manage grants for another Presence's Bookkeeping.
-- Owner (presence) grants a Cove member manage on /books. Agent chat tools
-- stay denied on Bookkeeping. Idempotent.

CREATE TABLE IF NOT EXISTS books_grants (
    owner_presence_id    UUID NOT NULL,
    grantee_presence_id  UUID NOT NULL,
    role                 TEXT NOT NULL DEFAULT 'manage',
    created_at           TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (owner_presence_id, grantee_presence_id)
);

CREATE INDEX IF NOT EXISTS idx_books_grants_grantee
    ON books_grants (grantee_presence_id);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'books_grants_role_check'
    ) THEN
        ALTER TABLE books_grants
            ADD CONSTRAINT books_grants_role_check
            CHECK (role IN ('manage'));
    END IF;
END $$;
