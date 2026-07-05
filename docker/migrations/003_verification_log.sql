-- =============================================================================
-- Migration 003: Soren Verification Log
-- =============================================================================
-- Layer 1 of the accountability architecture. Records every verification
-- attempt so patterns can be detected over time.
--
-- Run via Runbook 12:
--   cat docker/migrations/003_verification_log.sql | ssh ... "docker exec -i {container}-postgres psql -U {user} -d {db}"
-- =============================================================================

CREATE TABLE IF NOT EXISTS verification_log (
    id              SERIAL PRIMARY KEY,
    agent_id        TEXT NOT NULL,
    channel         TEXT DEFAULT '',
    tool_name       TEXT NOT NULL,
    tool_args       JSONB DEFAULT '{}',
    result_preview  TEXT DEFAULT '',
    passed          BOOLEAN NOT NULL,
    detail          TEXT DEFAULT '',
    verified_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Query patterns: by agent, by tool, by time, failures only
CREATE INDEX IF NOT EXISTS idx_vlog_agent_time
    ON verification_log(agent_id, verified_at DESC);
CREATE INDEX IF NOT EXISTS idx_vlog_tool_time
    ON verification_log(tool_name, verified_at DESC);
CREATE INDEX IF NOT EXISTS idx_vlog_failures
    ON verification_log(verified_at DESC) WHERE passed = FALSE;
