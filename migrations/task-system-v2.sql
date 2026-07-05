-- =============================================================================
-- Task System v2 Migration
-- Adds: sub-tasks, workflow state, audit trail, task-level comments
-- =============================================================================

-- ── New columns on tasks ────────────────────────────────────────────────────
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS parent_task_id INTEGER REFERENCES tasks(id);
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS workflow_pattern TEXT;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS workflow_state TEXT;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS audit_verdict TEXT;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS audit_count INTEGER DEFAULT 0;

-- ── Index for sub-task lookups ──────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent_task_id) WHERE parent_task_id IS NOT NULL;

-- ── Task-level comments (add task_id to project_comments) ───────────────────
ALTER TABLE project_comments ADD COLUMN IF NOT EXISTS task_id INTEGER REFERENCES tasks(id);
CREATE INDEX IF NOT EXISTS idx_comments_task ON project_comments(task_id) WHERE task_id IS NOT NULL;

-- ── Audit trail table ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS task_history (
    id           SERIAL PRIMARY KEY,
    task_id      INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    field_changed TEXT NOT NULL,
    old_value    TEXT,
    new_value    TEXT,
    changed_by   TEXT NOT NULL DEFAULT 'system',
    changed_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_task_history_task ON task_history(task_id);
CREATE INDEX IF NOT EXISTS idx_task_history_time ON task_history(changed_at DESC);
