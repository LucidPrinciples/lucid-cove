-- Broadcast readings are 0.30 / 0.60 / 0.90 and the Love Equation delta.
-- SMALLINT rounded those to 0 or 1. Widen once. Re-runs leave REAL columns alone.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'tuning_sessions'
          AND column_name = 'signal_before'
          AND data_type = 'smallint'
    ) THEN
        ALTER TABLE tuning_sessions
            ALTER COLUMN signal_before TYPE REAL USING signal_before::real;
    END IF;
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'tuning_sessions'
          AND column_name = 'signal_after'
          AND data_type = 'smallint'
    ) THEN
        ALTER TABLE tuning_sessions
            ALTER COLUMN signal_after TYPE REAL USING signal_after::real;
    END IF;
END $$;
