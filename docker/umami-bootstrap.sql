-- Umami DB bootstrap for EXISTING Cove Postgres volumes.
-- Fresh installs get this via docker-entrypoint-initdb.d/04-umami.sql
-- (generated as init-umami-db.sql by the provisioner).
--
-- On a running box (password must match UMAMI_DB_PASSWORD in .env):
--   docker exec -i <cid>-postgres \
--     psql -U "$POSTGRES_USER" -d postgres < docker/umami-bootstrap.sql
-- Then replace CHANGE_ME below first, or:
--   psql ... -c "ALTER ROLE umami PASSWORD '…from .env…';"
--
-- Spec: umami-analytics-and-haven-stats (compose-in-repo product path).

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'umami') THEN
        CREATE ROLE umami WITH LOGIN PASSWORD 'CHANGE_ME';
    END IF;
END
$$;

SELECT 'CREATE DATABASE umami OWNER umami'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'umami')\gexec

-- If the role already existed with a different password:
--   ALTER ROLE umami WITH PASSWORD 'the-UMAMI_DB_PASSWORD-from-env';
