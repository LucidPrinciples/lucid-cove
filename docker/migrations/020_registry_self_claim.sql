-- 020: Self-asserted namespace claims + reclamation (#4 / #200 / #161).
--
-- A self-hosted Cove proves ownership of its handle/Cove-name with its OWN operator
-- token (no email-based account required). Store the token's hash as the ownership
-- key on the registry row, plus a last_seen heartbeat so names unused for ~30 days
-- can be reclaimed. Additive + idempotent — safe on the live hub.

ALTER TABLE registry_handles ADD COLUMN IF NOT EXISTS owner_token_hash TEXT;
ALTER TABLE registry_handles ADD COLUMN IF NOT EXISTS last_seen TIMESTAMPTZ;

ALTER TABLE registry_coves ADD COLUMN IF NOT EXISTS owner_token_hash TEXT;
ALTER TABLE registry_coves ADD COLUMN IF NOT EXISTS last_seen TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_registry_handles_lastseen ON registry_handles(last_seen);
CREATE INDEX IF NOT EXISTS idx_registry_coves_lastseen ON registry_coves(last_seen);
