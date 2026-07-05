-- =============================================================================
-- Accountability Layer Migration
-- Adds: task source tracking, expected completion, escalation count
-- Creates: accountability_log table for Soren's monitoring sweeps
-- =============================================================================

-- ── New columns on tasks ────────────────────────────────────────────────────

-- Where did this task come from?
-- 'operator' = came from Jason/Chords, 'agent' = created by an agent, 'scheduled' = cron-generated
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'internal';

-- When should this be done? NULL = no deadline.
-- Operator-sourced tasks get a default window based on urgency at intake.
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS expected_by TIMESTAMPTZ;

-- How many times Soren has flagged this task.
-- 0 = fine, 1 = nudged Stuart, 2+ = notified operator
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS escalation_count INTEGER DEFAULT 0;

-- ── Indexes for accountability queries ──────────────────────────────────────

-- Soren's check_operator_requests needs to find operator-sourced tasks quickly
CREATE INDEX IF NOT EXISTS idx_tasks_source ON tasks(source) WHERE source = 'operator';

-- Soren's check_task_completion needs to find overdue tasks
CREATE INDEX IF NOT EXISTS idx_tasks_expected_by ON tasks(expected_by) WHERE expected_by IS NOT NULL;

-- ── Accountability log table ────────────────────────────────────────────────
-- Soren writes a record every time he runs an accountability sweep.
-- This is the audit trail for the monitoring system itself.

CREATE TABLE IF NOT EXISTS accountability_log (
    id              SERIAL PRIMARY KEY,
    sweep_at        TIMESTAMPTZ DEFAULT NOW(),
    tasks_checked   INTEGER NOT NULL,
    issues_found    INTEGER NOT NULL DEFAULT 0,
    escalations     JSONB DEFAULT '[]',
    -- Array of {task_id, issue_type, escalation_level, detail}
    notes           TEXT
);

CREATE INDEX IF NOT EXISTS idx_accountability_log_time ON accountability_log(sweep_at DESC);
