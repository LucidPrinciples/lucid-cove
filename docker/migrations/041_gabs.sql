-- 041: gabs — Gabs by Gabe link → assessment jobs (#GABS-V1 Phase 1 Quick).
-- Spec: AgentSkills/Working/Specs/gab-workflow-spec-2026-07-26.md
-- v1 = Quick path only (steward-quality assess). Full multi-agent later.

CREATE TABLE IF NOT EXISTS gabs (
    id              SERIAL PRIMARY KEY,
    presence_id     UUID,                             -- accounts.id; NULL in single mode
    url             TEXT NOT NULL,
    context         TEXT DEFAULT '',
    mode            TEXT NOT NULL DEFAULT 'quick'
                    CHECK (mode IN ('quick', 'full')),
    status          TEXT NOT NULL DEFAULT 'queued'
                    CHECK (status IN (
                        'queued',      -- Later / to process
                        'running',     -- in flight
                        'done',        -- History
                        'failed',
                        'cancelled'
                    )),
    title           TEXT DEFAULT '',
    bottom_line     TEXT DEFAULT '',
    fit             TEXT DEFAULT '',                  -- matters | adjacent | noise
    report_html     TEXT DEFAULT '',
    report_path     TEXT DEFAULT '',                  -- optional NC mirror path
    error           TEXT DEFAULT '',
    sources_json    TEXT DEFAULT '[]',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_gabs_presence_status
    ON gabs (presence_id, status, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_gabs_status_created
    ON gabs (status, created_at DESC);
