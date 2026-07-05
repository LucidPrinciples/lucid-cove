-- 022: GPU-share grants — a point-to-point credential a GPU provider Cove mints for a
-- friend's Cove to use this Cove's GPU for heavy work (video transcription first). The
-- provider hands the friend the raw token + this Cove's public GPU endpoint out-of-band;
-- the friend's Cove pastes it (video_asr=external + token). Store ONLY the token hash; the
-- raw token is shown once at mint. `revoked` is the on/off control. No discovery, no
-- billing — that's the (stubbed) marketplace tier. Idempotent.

CREATE TABLE IF NOT EXISTS gpu_grants (
    id           BIGSERIAL PRIMARY KEY,
    token_hash   TEXT NOT NULL UNIQUE,
    label        TEXT NOT NULL DEFAULT '',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at TIMESTAMPTZ,
    revoked      BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_gpu_grants_token_hash ON gpu_grants (token_hash);
