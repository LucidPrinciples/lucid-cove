-- Create the Dendrite (Matrix homeserver) role + database in this Cove's Postgres.
-- Runs after 00-base.sql in docker-entrypoint-initdb.d.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'dendrite') THEN
        CREATE ROLE dendrite WITH LOGIN PASSWORD '8GeKmY3FPggt5iDAIufS6dgJxRde05os';
    END IF;
END
$$;

SELECT 'CREATE DATABASE dendrite OWNER dendrite'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'dendrite')\gexec
