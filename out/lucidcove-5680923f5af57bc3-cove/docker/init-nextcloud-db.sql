-- Create the Nextcloud role + database in this Cove's Postgres.
-- Runs after 00-base.sql in docker-entrypoint-initdb.d.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'nextcloud') THEN
        CREATE ROLE nextcloud WITH LOGIN PASSWORD 'ElRku1cb6ATuS9vTFoVFOCg5hBfBKWIr';
    END IF;
END
$$;

SELECT 'CREATE DATABASE nextcloud OWNER nextcloud'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'nextcloud')\gexec
