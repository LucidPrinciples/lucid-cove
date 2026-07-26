-- 042: gabs.presence_id must be UUID (accounts.id), not INTEGER.
-- 041 shipped with INTEGER by mistake; live Founders/Clearfield already applied it.
-- Safe on fresh installs where 041 already created UUID: no-op when type is uuid.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'gabs'
          AND column_name = 'presence_id'
          AND data_type = 'integer'
    ) THEN
        -- No real rows should hold integer presence ids (inserts failed). Drop default none.
        ALTER TABLE gabs
            ALTER COLUMN presence_id TYPE UUID
            USING NULL;
    END IF;
END $$;
