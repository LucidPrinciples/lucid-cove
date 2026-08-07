-- 043: Cove project membership (attach presences to manager-owned projects).
-- Cove projects keep presence_id NULL. Attached members see them in MC and
-- receive an NC share of Projects/{name}/. Idempotent.

CREATE TABLE IF NOT EXISTS project_members (
    project_id   INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    presence_id  UUID NOT NULL,
    role         TEXT NOT NULL DEFAULT 'work',
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (project_id, presence_id)
);

CREATE INDEX IF NOT EXISTS idx_project_members_presence
    ON project_members (presence_id);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'project_members_role_check'
    ) THEN
        ALTER TABLE project_members
            ADD CONSTRAINT project_members_role_check
            CHECK (role IN ('view', 'work', 'admin'));
    END IF;
END $$;
