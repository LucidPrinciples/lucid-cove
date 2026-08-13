-- 046: ezra_knowledge — domain knowledge sessions (Functional Health v1).
-- Isolated from steward/agent memory. Promote notes stay in this product.

CREATE TABLE IF NOT EXISTS knowledge_sessions (
    id              SERIAL PRIMARY KEY,
    presence_id     UUID,
    title           TEXT NOT NULL DEFAULT '',
    model_tag       TEXT NOT NULL,
    system_prompt   TEXT DEFAULT '',
    temperature     DOUBLE PRECISION NOT NULL DEFAULT 0.7,
    status          TEXT NOT NULL DEFAULT 'open'
                    CHECK (status IN ('open', 'closed')),
    thread_kind     TEXT NOT NULL DEFAULT 'functional-health',
    notes           TEXT DEFAULT '',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    closed_at       TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_knowledge_sessions_presence
    ON knowledge_sessions (presence_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_knowledge_sessions_status
    ON knowledge_sessions (status, updated_at DESC);

ALTER TABLE knowledge_sessions
    ADD COLUMN IF NOT EXISTS thread_kind TEXT NOT NULL DEFAULT 'functional-health';

CREATE INDEX IF NOT EXISTS idx_knowledge_sessions_kind
    ON knowledge_sessions (thread_kind, updated_at DESC);


CREATE TABLE IF NOT EXISTS knowledge_messages (
    id              SERIAL PRIMARY KEY,
    session_id      INTEGER NOT NULL
                    REFERENCES knowledge_sessions(id) ON DELETE CASCADE,
    role            TEXT NOT NULL
                    CHECK (role IN ('user', 'assistant', 'system')),
    content         TEXT NOT NULL DEFAULT '',
    model_tag       TEXT DEFAULT '',
    latency_ms      INTEGER,
    error           TEXT DEFAULT '',
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_knowledge_messages_session
    ON knowledge_messages (session_id, id ASC);
