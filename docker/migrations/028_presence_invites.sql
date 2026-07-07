-- 028 — Presence invites (self-onboard). An admin mints a single-use, role-baked link;
-- the invitee opens it on THEIR device, runs the wizard as themselves (on the Cove's
-- model), and lands signed into their own MC. Idempotent — safe to re-run.
CREATE TABLE IF NOT EXISTS presence_invites (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    token_hash      TEXT UNIQUE NOT NULL,   -- sha256 of the raw invite token (raw never stored)
    cove_id         TEXT,                   -- the Cove this invite joins
    role            TEXT NOT NULL DEFAULT 'member',  -- admin | member (baked by the inviter)
    reserved_handle TEXT,                   -- optional pre-assigned handle; else invitee picks
    invited_label   TEXT,                   -- optional "for Mom" note the admin sets
    inviter_id      UUID,                   -- the admin account that minted it
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    expires_at      TIMESTAMPTZ,            -- NULL = no expiry (default set in code = +7d)
    consumed_at     TIMESTAMPTZ,            -- set when the invitee completes (single-use)
    consumed_by     UUID                    -- the new presence account id
);
CREATE INDEX IF NOT EXISTS idx_presence_invites_token ON presence_invites(token_hash);
CREATE INDEX IF NOT EXISTS idx_presence_invites_open  ON presence_invites(cove_id) WHERE consumed_at IS NULL;
