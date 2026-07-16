-- 037: truth_gate_events — #D57 (behavior-calibration-spec §A4).
-- One row per Truth Gate FIRE (accommodation with evidence, or fabrication).
-- Passes are not logged. Surfaced in the admin Intelligence panel + /ops.
-- Idempotent.

CREATE TABLE IF NOT EXISTS truth_gate_events (
    id             BIGSERIAL PRIMARY KEY,
    ts             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    agent_id       TEXT NOT NULL,
    channel        TEXT NOT NULL DEFAULT '',
    judge_model    TEXT NOT NULL DEFAULT '',
    accommodation  BOOLEAN NOT NULL DEFAULT FALSE,
    fabrication    BOOLEAN NOT NULL DEFAULT FALSE,
    description    TEXT NOT NULL DEFAULT '',
    truth_available TEXT NOT NULL DEFAULT '',
    evidence_quote TEXT NOT NULL DEFAULT '',
    regenerated    BOOLEAN NOT NULL DEFAULT FALSE,
    latency_ms     INTEGER
);

CREATE INDEX IF NOT EXISTS idx_truth_gate_events_ts ON truth_gate_events (ts DESC);
CREATE INDEX IF NOT EXISTS idx_truth_gate_events_agent ON truth_gate_events (agent_id, ts DESC);
