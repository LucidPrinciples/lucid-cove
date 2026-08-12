-- 045: model_lab — Soren Model Lab + Tester (#MODELLAB1 v1).
-- Spec: AgentSkills/Working/Specs/model-lab-v1-2026-08-11.md
-- v1 = Ollama pick + focused sessions + structured single/A-B runs.
-- Does NOT write agent/steward memory. Promote → Ezra Knowledge is stage 2.

CREATE TABLE IF NOT EXISTS model_lab_sessions (
    id              SERIAL PRIMARY KEY,
    presence_id     UUID,                             -- accounts.id; NULL in single mode
    title           TEXT NOT NULL DEFAULT '',
    model_tag       TEXT NOT NULL,                    -- Ollama tag, e.g. qwen3:8b
    system_prompt   TEXT DEFAULT '',
    temperature     DOUBLE PRECISION NOT NULL DEFAULT 0.7,
    status          TEXT NOT NULL DEFAULT 'open'
                    CHECK (status IN ('open', 'closed')),
    notes           TEXT DEFAULT '',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    closed_at       TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_model_lab_sessions_presence
    ON model_lab_sessions (presence_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_model_lab_sessions_status
    ON model_lab_sessions (status, updated_at DESC);


CREATE TABLE IF NOT EXISTS model_lab_messages (
    id              SERIAL PRIMARY KEY,
    session_id      INTEGER NOT NULL
                    REFERENCES model_lab_sessions(id) ON DELETE CASCADE,
    role            TEXT NOT NULL
                    CHECK (role IN ('user', 'assistant', 'system')),
    content         TEXT NOT NULL DEFAULT '',
    model_tag       TEXT DEFAULT '',                  -- which tag produced assistant turn
    latency_ms      INTEGER,
    error           TEXT DEFAULT '',
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_model_lab_messages_session
    ON model_lab_messages (session_id, id ASC);


CREATE TABLE IF NOT EXISTS model_lab_runs (
    id              SERIAL PRIMARY KEY,
    presence_id     UUID,
    title           TEXT NOT NULL DEFAULT '',
    kind            TEXT NOT NULL DEFAULT 'single'
                    CHECK (kind IN ('single', 'ab')),
    model_a         TEXT NOT NULL,
    model_b         TEXT DEFAULT '',                  -- required for kind=ab
    system_prompt   TEXT DEFAULT '',
    user_prompt     TEXT NOT NULL DEFAULT '',
    temperature     DOUBLE PRECISION NOT NULL DEFAULT 0.3,
    status          TEXT NOT NULL DEFAULT 'queued'
                    CHECK (status IN (
                        'queued',
                        'running',
                        'done',
                        'failed',
                        'cancelled'
                    )),
    response_a      TEXT DEFAULT '',
    response_b      TEXT DEFAULT '',
    latency_a_ms    INTEGER,
    latency_b_ms    INTEGER,
    error           TEXT DEFAULT '',
    notes           TEXT DEFAULT '',                  -- operator judgment / accept notes
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_model_lab_runs_presence_status
    ON model_lab_runs (presence_id, status, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_model_lab_runs_status_created
    ON model_lab_runs (status, created_at DESC);
