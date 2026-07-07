-- 029 — Seed-once for self-onboard invites. /join seeds a member operator and signs
-- them in (so the invitee runs the operator-profile step + agent wizard as themselves,
-- exactly like the founder). We store the seeded account id on the invite row so
-- re-opening the same link REUSES that seed instead of spawning a duplicate. Idempotent.
ALTER TABLE presence_invites
    ADD COLUMN IF NOT EXISTS seeded_account_id UUID;
