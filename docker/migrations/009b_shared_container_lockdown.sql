-- =============================================================================
-- Migration 009: Shared Container Schema Lockdown
-- =============================================================================
-- Adds all tables and columns needed for the shared container to serve
-- Free -> Pro -> Operator tiers with proper multi-user isolation.
--
-- Safe to run on any container (all IF NOT EXISTS / IF NOT EXISTS guards).
--
-- Apply: cat migrations/009_shared_container_lockdown.sql | psql ...
-- =============================================================================


-- ─── 1. Projects + Tasks (Operator tier project management) ─────────────────
-- Previously only created by the provisioner for single-Cove containers.
-- Now part of base schema so Operators on shared container get them too.

CREATE TABLE IF NOT EXISTS projects (
    id          SERIAL PRIMARY KEY,
    presence_id UUID,                -- User who owns this project (multi-mode)
    slug        TEXT NOT NULL,
    name        TEXT NOT NULL,
    description TEXT,
    status      TEXT DEFAULT 'active',
    owner       TEXT,                -- Display name (single-mode compat)
    team        TEXT[] DEFAULT '{}',
    goals       TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Add presence_id if table already exists without it (provisioner-created)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'projects' AND column_name = 'presence_id'
    ) THEN
        ALTER TABLE projects ADD COLUMN presence_id UUID;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_projects_presence ON projects(presence_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_projects_slug_presence
    ON projects(slug, presence_id) WHERE presence_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS tasks (
    id               SERIAL PRIMARY KEY,
    project_id       INTEGER REFERENCES projects(id) ON DELETE CASCADE,
    parent_task_id   INTEGER REFERENCES tasks(id),
    title            TEXT NOT NULL,
    description      TEXT,
    status           TEXT DEFAULT 'pending',
    priority         TEXT DEFAULT 'normal',
    assignee         TEXT,
    due_date         DATE,
    completed_at     TIMESTAMPTZ,
    created_by       TEXT,
    notes            TEXT,
    workflow_pattern TEXT,
    workflow_state   TEXT,
    audit_verdict    TEXT,
    audit_count      INTEGER DEFAULT 0,
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    updated_at       TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent_task_id) WHERE parent_task_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS project_comments (
    id              SERIAL PRIMARY KEY,
    project_id      INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    task_id         INTEGER REFERENCES tasks(id),
    author          TEXT NOT NULL,
    content         TEXT NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_comments_task ON project_comments(task_id) WHERE task_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS task_history (
    id            SERIAL PRIMARY KEY,
    task_id       INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    field_changed TEXT NOT NULL,
    old_value     TEXT,
    new_value     TEXT,
    changed_by    TEXT NOT NULL DEFAULT 'system',
    changed_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_task_history_task ON task_history(task_id);
CREATE INDEX IF NOT EXISTS idx_task_history_time ON task_history(changed_at DESC);


-- ─── 2. Quick Lists — Soft Delete + Activity History ────────────────────────
-- Replace hard DELETE with archived flag so users never lose data.

-- Add archived columns to quick_lists
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'quick_lists' AND column_name = 'archived'
    ) THEN
        ALTER TABLE quick_lists ADD COLUMN archived BOOLEAN DEFAULT FALSE;
        ALTER TABLE quick_lists ADD COLUMN archived_at TIMESTAMPTZ;
    END IF;
END $$;

-- Add archived columns to quick_list_items
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'quick_list_items' AND column_name = 'archived'
    ) THEN
        ALTER TABLE quick_list_items ADD COLUMN archived BOOLEAN DEFAULT FALSE;
        ALTER TABLE quick_list_items ADD COLUMN archived_at TIMESTAMPTZ;
    END IF;
END $$;

-- Activity log for list history (items added, checked, unchecked, archived)
CREATE TABLE IF NOT EXISTS quick_list_activity (
    id              SERIAL PRIMARY KEY,
    list_id         INTEGER NOT NULL REFERENCES quick_lists(id) ON DELETE CASCADE,
    item_id         INTEGER REFERENCES quick_list_items(id) ON DELETE SET NULL,
    presence_id     UUID,
    action          TEXT NOT NULL,       -- 'item_added', 'item_checked', 'item_unchecked',
                                         -- 'item_archived', 'item_restored', 'item_edited',
                                         -- 'list_archived', 'list_restored', 'list_renamed',
                                         -- 'checked_archived' (bulk clear)
    detail          TEXT,                -- Optional context (old text for edits, etc.)
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_qla_list ON quick_list_activity(list_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_qla_presence ON quick_list_activity(presence_id);


-- ─── 3. Contact Messages (verify exists) ────────────────────────────────────

CREATE TABLE IF NOT EXISTS contact_messages (
    id              SERIAL PRIMARY KEY,
    account_id      UUID REFERENCES accounts(id),
    email           TEXT NOT NULL,
    display_name    TEXT,
    username        TEXT,
    tier            TEXT,
    subject         TEXT DEFAULT '',
    message         TEXT NOT NULL,
    archived        BOOLEAN DEFAULT FALSE,
    archived_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_contact_messages_archived
    ON contact_messages(archived, created_at DESC);
