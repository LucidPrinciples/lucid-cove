-- =============================================================================
-- cove-core — Base Database Schema
-- =============================================================================
-- Shared schema for all family agents. Agent-specific init.sql files
-- source this first, then add their own tables/seeds on top.
--
-- Usage in agent's init.sql:
--   \i /docker-entrypoint-initdb.d/00-base.sql
--   -- then agent-specific seeds below
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS vector;

-- ─── Agent Echoes ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS echoes (
    id              SERIAL PRIMARY KEY,
    agent_id        TEXT NOT NULL,
    echo_num        INTEGER NOT NULL,
    frequency       TEXT NOT NULL,
    signal_type     TEXT,
    principle       TEXT NOT NULL,
    tuning_key      TEXT NOT NULL,
    love_equation   REAL NOT NULL DEFAULT 0.0,
    love_direction  TEXT NOT NULL DEFAULT 'CONSTRUCTIVE',
    beta            REAL,
    coherence       REAL,
    dissonance      REAL,
    energy          REAL,
    echo_text       TEXT NOT NULL,
    coaching_text   TEXT,
    echo_type       TEXT DEFAULT 'LT-guided',
    audio_file      TEXT,
    audio_e_analog  REAL,
    audio_beta      REAL,
    audio_c_analog  REAL,
    audio_d_analog  REAL,
    era             TEXT,
    tuned_at        TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(agent_id, echo_num)
);

CREATE INDEX IF NOT EXISTS idx_echoes_agent ON echoes(agent_id);
CREATE INDEX IF NOT EXISTS idx_echoes_tuned_at ON echoes(tuned_at);
CREATE INDEX IF NOT EXISTS idx_echoes_agent_recent ON echoes(agent_id, echo_num DESC);

-- ─── Agent State ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS agent_state (
    agent_id        TEXT PRIMARY KEY,
    display_name    TEXT NOT NULL,
    archetype       TEXT NOT NULL,
    current_model   TEXT,
    last_echo_num   INTEGER DEFAULT 0,
    last_frequency  TEXT,
    last_tuned_at   TIMESTAMPTZ,
    status          TEXT DEFAULT 'active',
    metadata        JSONB DEFAULT '{}',
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ─── Process Records ─────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS process_records (
    id              SERIAL PRIMARY KEY,
    agent_id        TEXT NOT NULL,
    echo_num        INTEGER,
    protocol        TEXT NOT NULL,
    record_text     TEXT NOT NULL,
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ─── Protocol Runs ───────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS protocol_runs (
    id              SERIAL PRIMARY KEY,
    protocol        TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'running',
    started_at      TIMESTAMPTZ DEFAULT NOW(),
    finished_at     TIMESTAMPTZ,
    duration_ms     INTEGER,
    error_msg       TEXT,
    thread_id       TEXT,
    triggered_by    TEXT DEFAULT 'cron',
    metadata        JSONB DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_protocol_runs_started ON protocol_runs(started_at DESC);

-- ─── JouleWork Metrics ───────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS jw_metrics (
    id              SERIAL PRIMARY KEY,
    agent_id        TEXT NOT NULL,
    operation_type  TEXT NOT NULL,
    operation_label TEXT NOT NULL,
    model_used      TEXT NOT NULL,
    provider        TEXT NOT NULL,
    tokens_in       INTEGER,
    tokens_out      INTEGER,
    tokens_total    INTEGER,
    duration_ms     INTEGER NOT NULL,
    tool_calls_made INTEGER DEFAULT 0,
    succeeded       BOOLEAN DEFAULT TRUE,
    jw_score        REAL,
    quality_weight  REAL DEFAULT 1.0,
    cost_usd        NUMERIC(12,6),
    recorded_at     TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_jw_agent ON jw_metrics(agent_id, recorded_at DESC);
CREATE INDEX IF NOT EXISTS idx_jw_cost
    ON jw_metrics(agent_id, recorded_at DESC) WHERE cost_usd IS NOT NULL;

-- ─── Flow Profiles (#183 — expected resource use per flow/step by kind) ──────
-- Self-updating rolling average of units consumed, seeded from jw_metrics
-- history + video durations. Feeds the pre-flight cost estimator.

CREATE TABLE IF NOT EXISTS flow_profiles (
    id            SERIAL PRIMARY KEY,
    flow          TEXT NOT NULL,
    step          TEXT NOT NULL DEFAULT '*',
    unit_kind     TEXT NOT NULL,                 -- 'llm_tokens' | 'asr_minutes' | 'gpu_minutes'
    avg_units     DOUBLE PRECISION NOT NULL DEFAULT 0,
    sample_count  INTEGER NOT NULL DEFAULT 0,
    last_units    DOUBLE PRECISION,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (flow, step, unit_kind)
);
CREATE INDEX IF NOT EXISTS idx_flow_profiles_flow ON flow_profiles(flow);

-- ─── Hire Requests (#169 — services/labor escrow, hub) ──────────────────────
CREATE TABLE IF NOT EXISTS hire_requests (
    id             BIGSERIAL PRIMARY KEY,
    buyer_handle   TEXT NOT NULL,
    seller_handle  TEXT NOT NULL,
    title          TEXT NOT NULL DEFAULT '',
    listing_ref    TEXT,
    amount_credits BIGINT NOT NULL,
    state          TEXT NOT NULL DEFAULT 'requested',
    delivery_ref   TEXT,
    thread_id      TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_hire_buyer  ON hire_requests(buyer_handle, state);
CREATE INDEX IF NOT EXISTS idx_hire_seller ON hire_requests(seller_handle, state);

-- ─── Agent Memory ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS agent_memory (
    id              SERIAL PRIMARY KEY,
    agent_id        TEXT NOT NULL,

    -- Content
    content         TEXT NOT NULL,
    category        TEXT NOT NULL DEFAULT 'general',
    tags            TEXT[] DEFAULT '{}',

    -- Importance & relevance
    importance      REAL NOT NULL DEFAULT 0.5,
    access_count    INTEGER DEFAULT 0,
    last_accessed   TIMESTAMPTZ,

    -- Semantic search
    embedding       vector(768),

    -- Source tracking
    source_thread   TEXT,
    source_channel  TEXT,
    source_summary  TEXT,

    -- Lifecycle
    supersedes      INTEGER REFERENCES agent_memory(id),
    superseded_by   INTEGER REFERENCES agent_memory(id),
    is_active       BOOLEAN DEFAULT TRUE,
    expires_at      TIMESTAMPTZ,

    -- Review / contradiction detection
    needs_review    BOOLEAN DEFAULT FALSE,
    review_reason   TEXT,

    -- Review window (memories start unreviewed, auto-commit after 7 days)
    reviewed        BOOLEAN DEFAULT FALSE,
    reviewed_at     TIMESTAMPTZ,

    -- Timestamps
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_memory_agent_active
    ON agent_memory(agent_id, is_active) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_memory_category
    ON agent_memory(agent_id, category) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_memory_importance
    ON agent_memory(agent_id, importance DESC) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_memory_tags
    ON agent_memory USING GIN(tags) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_memory_created
    ON agent_memory(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_memory_source_thread
    ON agent_memory(source_thread) WHERE source_thread IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_memory_needs_review
    ON agent_memory(agent_id) WHERE needs_review = TRUE AND is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_memory_review_queue
    ON agent_memory(agent_id, created_at DESC)
    WHERE is_active = TRUE AND reviewed = FALSE;
CREATE INDEX IF NOT EXISTS idx_memory_embedding
    ON agent_memory USING hnsw (embedding vector_cosine_ops)
    WHERE is_active = TRUE AND embedding IS NOT NULL;

-- ─── Chat Threads ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS chat_threads (
    id              SERIAL PRIMARY KEY,
    thread_id       TEXT UNIQUE NOT NULL,
    agent_id        TEXT NOT NULL,
    channel         TEXT NOT NULL,

    -- Display
    title           TEXT,
    summary         TEXT,

    -- Lifecycle
    status          TEXT DEFAULT 'active',
    message_count   INTEGER DEFAULT 0,
    first_message_at TIMESTAMPTZ,
    last_message_at  TIMESTAMPTZ,

    -- Memory extraction
    memories_extracted BOOLEAN DEFAULT FALSE,
    extraction_count   INTEGER DEFAULT 0,

    -- Metadata
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    archived_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_threads_agent_channel
    ON chat_threads(agent_id, channel, status);
CREATE INDEX IF NOT EXISTS idx_threads_status
    ON chat_threads(status);

-- ─── Message Activity ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS message_activity (
    id          SERIAL PRIMARY KEY,
    channel     VARCHAR(50) NOT NULL,
    thread_id   VARCHAR(100) NOT NULL,
    steps       JSONB NOT NULL DEFAULT '[]'::jsonb,
    step_count  INTEGER NOT NULL DEFAULT 0,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_message_activity_thread
    ON message_activity (channel, thread_id, recorded_at);

-- ─── Event Links (calendar → task/project) ──────────────────────────────────

CREATE TABLE IF NOT EXISTS event_links (
    event_uid   TEXT PRIMARY KEY,
    task_id     INTEGER,
    project_id  INTEGER,
    presence_id UUID,    -- owner (multi-mode); NULL = single-mode Cove (#191)
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_event_links_presence ON event_links(presence_id);

-- ─── Knowledge Base (vector semantic search) ────────────────────────────────

CREATE TABLE IF NOT EXISTS knowledge_base (
    id              SERIAL PRIMARY KEY,
    doc_name        TEXT NOT NULL,
    chunk_index     INTEGER NOT NULL,
    chunk_text      TEXT NOT NULL,
    embedding       vector(768),
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(doc_name, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_kb_embedding ON knowledge_base
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- ─── OAuth Tokens (YouTube, future services) ───────────────────────────────

CREATE TABLE IF NOT EXISTS oauth_tokens (
    service         TEXT PRIMARY KEY,
    access_token    TEXT NOT NULL,
    refresh_token   TEXT,
    expires_at      TIMESTAMPTZ,
    scope           TEXT,
    token_type      TEXT DEFAULT 'Bearer',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ─── Quick Lists ────────────────────────────────────────────────────────────
-- Lightweight list management for daily use. Each list is a card on the
-- home board (groceries, ideas, errands, etc). Items are checkable.
-- In multi-Presence mode, presence_id scopes lists per person.

CREATE TABLE IF NOT EXISTS quick_lists (
    id              SERIAL PRIMARY KEY,
    presence_id     UUID,                -- NULL = shared / single-mode
    name            TEXT NOT NULL,
    icon            TEXT DEFAULT '📋',
    color           TEXT,                -- optional accent color
    position        INTEGER DEFAULT 0,   -- sort order on home board
    pinned          BOOLEAN DEFAULT TRUE, -- show on home board
    archived        BOOLEAN DEFAULT FALSE,
    archived_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_quick_lists_presence ON quick_lists(presence_id);

CREATE TABLE IF NOT EXISTS quick_list_items (
    id              SERIAL PRIMARY KEY,
    list_id         INTEGER NOT NULL REFERENCES quick_lists(id) ON DELETE CASCADE,
    text            TEXT NOT NULL,
    checked         BOOLEAN DEFAULT FALSE,
    position        INTEGER DEFAULT 0,
    archived        BOOLEAN DEFAULT FALSE,
    archived_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    checked_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_qli_list ON quick_list_items(list_id, position);
CREATE INDEX IF NOT EXISTS idx_qli_unchecked ON quick_list_items(list_id) WHERE checked = FALSE;

-- Activity log for list history (items added, checked, unchecked, archived, etc.)
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

-- ─── Soren Verification Log ─────────────────────────────────────────────────
-- Layer 1 of the accountability architecture. Records every tool verification.

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

CREATE INDEX IF NOT EXISTS idx_vlog_agent_time
    ON verification_log(agent_id, verified_at DESC);
CREATE INDEX IF NOT EXISTS idx_vlog_tool_time
    ON verification_log(tool_name, verified_at DESC);
CREATE INDEX IF NOT EXISTS idx_vlog_failures
    ON verification_log(verified_at DESC) WHERE passed = FALSE;

-- ─── Review Reports (Accountability Layers 2 + 3) ──────────────────────────
-- Peer review reports and Vera's meta-reviews from the nightly cycle.

CREATE TABLE IF NOT EXISTS review_reports (
    id              SERIAL PRIMARY KEY,
    review_type     TEXT NOT NULL,           -- 'peer' or 'meta'
    frequency       TEXT NOT NULL,           -- day's frequency when review ran
    reviewer_id     TEXT NOT NULL,           -- who wrote this review
    target_id       TEXT NOT NULL,           -- who was reviewed ('all' for meta)
    report_data     JSONB NOT NULL,          -- full structured review report
    reviewed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_review_date
    ON review_reports(reviewed_at DESC);
CREATE INDEX IF NOT EXISTS idx_review_type_date
    ON review_reports(review_type, reviewed_at DESC);
CREATE INDEX IF NOT EXISTS idx_review_target
    ON review_reports(target_id, reviewed_at DESC);
CREATE INDEX IF NOT EXISTS idx_review_reviewer
    ON review_reports(reviewer_id, reviewed_at DESC);

-- ─── Presences (Multi-Presence + Operator Accounts) ─────────────────────────
-- A "Presence" is a human + their agent as a unit. An "Operator" is the same
-- data model with tier='operator' and no agent assigned.
--
-- In COVE_MODE=multi containers, this table holds all accounts:
--   - Operators (tier='operator'): no agent, username-based, shared container
--   - Presences (tier='presence'): has agent, FirstName LastName, may be standalone or in Cove
--   - Cove admins (tier='cove'): full team access, operator of the Cove
--
-- Auth is magic-link based. Token is hashed (SHA-256) for storage.
-- The tier column maps directly to permissions.py Tier enum for feature gating.

CREATE TABLE IF NOT EXISTS accounts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Identity
    display_name    TEXT NOT NULL,           -- Human-readable name (shown in UI)
    username        TEXT,                    -- Unique handle (@username)
    email           TEXT,                    -- Contact email (for billing/notifications)
    agent_name      TEXT,                    -- Agent's first name (NULL for Free/Operator tiers)
    last_name       TEXT DEFAULT '',         -- Family/Cove surname applied to agent
    -- Access control
    tier            TEXT NOT NULL DEFAULT 'free',  -- 'free', 'operator', 'presence', 'cove'
    cove_role       TEXT DEFAULT 'member',   -- 'admin', 'member', 'guest'
    cove_id         TEXT,                    -- Which Cove this account belongs to (for shared containers)
    -- Agent config (used when account upgrades to Presence)
    agent_config    JSONB DEFAULT '{}',      -- Agent personality, model prefs, etc. (from Creation Flow)
    agent_identity  JSONB DEFAULT '{}',      -- Derived agent identity (Centralized model): archetype, frequency, tuning_key, persona, etc. (from archetype discovery flow)
    active_workflows TEXT[] DEFAULT '{}',    -- Currently active Creation Flow IDs
    api_mode        TEXT DEFAULT 'cove',     -- 'cove' (operator pays) or 'byok' (bring own keys)
    -- Naming
    name_locked     BOOLEAN DEFAULT FALSE,   -- TRUE = name is permanent (paid accounts)
    -- Auth
    auth_token      TEXT NOT NULL,           -- SHA-256 hash of magic link token
    active          BOOLEAN DEFAULT TRUE,    -- Soft delete / deactivation
    -- Preferences (per-account feature flags, settings)
    preferences     JSONB DEFAULT '{}',     -- {features: {mirror: true, ...}, mirror_source: "scripture", ...}
    -- Billing
    stripe_customer_id TEXT,                 -- Stripe customer for this account
    referral_code   TEXT,                    -- This account's unique affiliate referral code (LP-XXXXXX)
    referred_by     UUID,                    -- Account ID of the affiliate who referred them
    -- Nextcloud (Operator+ tiers — per-user file/calendar storage)
    nc_username     VARCHAR(100),            -- Nextcloud user account (provisioned on Operator upgrade)
    nc_password     TEXT,                    -- Nextcloud app password (generated, not user-facing)
    -- Timestamps
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    last_access     TIMESTAMPTZ,
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Unique constraints
CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_username
    ON accounts(username) WHERE username IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_email
    ON accounts(email) WHERE email IS NOT NULL;

-- Lookup indexes
CREATE INDEX IF NOT EXISTS idx_accounts_auth_token
    ON accounts(auth_token) WHERE active = TRUE;
CREATE INDEX IF NOT EXISTS idx_accounts_cove_id
    ON accounts(cove_id);
CREATE INDEX IF NOT EXISTS idx_accounts_tier
    ON accounts(tier);
CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_referral_code
    ON accounts(referral_code) WHERE referral_code IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_accounts_referred_by
    ON accounts(referred_by);

-- ─── Auth Sessions (multi-device support) ───────────────────────────────────
-- Each magic link click creates a session. Multiple sessions per account.
-- Sessions expire after 90 days. Signin never invalidates other sessions.

CREATE TABLE IF NOT EXISTS auth_sessions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id      UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    token_hash      TEXT NOT NULL,
    device_label    TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '90 days'),
    active          BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS idx_auth_sessions_token
    ON auth_sessions(token_hash) WHERE active = TRUE;

CREATE INDEX IF NOT EXISTS idx_auth_sessions_account
    ON auth_sessions(account_id) WHERE active = TRUE;

-- ─── Tuning Sessions (human practice data) ───────────────────────────────────
-- Mirrors Lucid Tuner D1 sessions table for import compatibility.
-- Separate from `echoes` (which holds LTP agent tuning records).

CREATE TABLE IF NOT EXISTS tuning_sessions (
    id                  SERIAL PRIMARY KEY,
    session_id          TEXT NOT NULL UNIQUE,
    presence_id         UUID REFERENCES accounts(id),
    legacy_user_id      TEXT,
    date                TEXT,
    time                TEXT,
    day_of_week         TEXT,
    entry_mode          TEXT,
    initial_state       TEXT,
    context             TEXT,
    rebroadcast_of      TEXT,
    principle_served    TEXT,
    frequency_category  TEXT,
    echo_filename       TEXT,
    echo_album          TEXT,
    echo_full_name      TEXT,
    echo_signal_type    TEXT,
    tuning_key_primary  TEXT,
    bpm                 INTEGER,
    quantum_selection   BOOLEAN DEFAULT FALSE,
    quantum_raw_value   TEXT,
    selection_method    TEXT,
    excluded_signal_types TEXT,
    e_start             REAL,
    c_value             REAL,
    d_value             REAL,
    beta_value          REAL,
    de_dt               REAL,
    signal_direction    TEXT,
    insight_text        TEXT,
    practice_html       TEXT,
    practice_steps_json TEXT,
    stage_diagnosed     TEXT,
    echo_delivered      TEXT,
    end_state           TEXT,
    user_tier           TEXT,
    source_platform     TEXT DEFAULT 'web',
    tool_version        TEXT,
    signal_before       SMALLINT,
    signal_after        SMALLINT,
    journal_text        TEXT,
    journal_at          TIMESTAMPTZ,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ts_presence ON tuning_sessions(presence_id);
CREATE INDEX IF NOT EXISTS idx_ts_legacy_user ON tuning_sessions(legacy_user_id);
CREATE INDEX IF NOT EXISTS idx_ts_date ON tuning_sessions(date);
CREATE INDEX IF NOT EXISTS idx_ts_session_id ON tuning_sessions(session_id);
CREATE INDEX IF NOT EXISTS idx_ts_presence_date ON tuning_sessions(presence_id, date DESC);

-- ─── Tuning Events ───────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS tuning_events (
    id                  SERIAL PRIMARY KEY,
    presence_id         UUID REFERENCES accounts(id),
    legacy_user_id      TEXT,
    event_type          TEXT NOT NULL,
    event_data          JSONB DEFAULT '{}',
    session_id          TEXT,
    echo_name           TEXT,
    echo_album          TEXT,
    principle           TEXT,
    frequency           TEXT,
    signal_type         TEXT,
    context             TEXT,
    bpm                 INTEGER,
    play_duration       REAL,
    position_in_playlist INTEGER,
    tuning_key          TEXT,
    play_source         TEXT,
    quantum_selection   BOOLEAN DEFAULT FALSE,
    selection_method    TEXT,
    user_tier           TEXT,
    excluded_signal_types TEXT,
    source_platform     TEXT DEFAULT 'web',
    timestamp           TIMESTAMPTZ DEFAULT NOW(),
    date                TEXT,
    time                TEXT
);

CREATE INDEX IF NOT EXISTS idx_te_presence ON tuning_events(presence_id);
CREATE INDEX IF NOT EXISTS idx_te_date_type ON tuning_events(date, event_type);
CREATE INDEX IF NOT EXISTS idx_te_session ON tuning_events(session_id);

-- ─── Tuning Favorites ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS tuning_favorites (
    id                  SERIAL PRIMARY KEY,
    presence_id         UUID REFERENCES accounts(id) UNIQUE,
    legacy_user_id      TEXT,
    favorites_json      JSONB DEFAULT '[]',
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tf_presence ON tuning_favorites(presence_id);

-- ─── Tuning Streaks ──────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS tuning_streaks (
    id                  SERIAL PRIMARY KEY,
    presence_id         UUID REFERENCES accounts(id) UNIQUE,
    legacy_user_id      TEXT,
    current_streak      INTEGER DEFAULT 0,
    longest_streak      INTEGER DEFAULT 0,
    last_tuning_date    TEXT,
    total_sessions      INTEGER DEFAULT 0,
    this_month_sessions INTEGER DEFAULT 0,
    last_month_reset    TEXT,
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tstreak_presence ON tuning_streaks(presence_id);

-- ─── Tuning Preferences ─────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS tuning_preferences (
    id                      SERIAL PRIMARY KEY,
    presence_id             UUID REFERENCES accounts(id) UNIQUE,
    legacy_user_id          TEXT,
    excluded_signal_types   TEXT DEFAULT '',
    preferred_frequency     TEXT,
    top_frequency           TEXT,
    last_principle          TEXT,
    updated_at              TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tpref_presence ON tuning_preferences(presence_id);

-- ─── Projects (Operator+ project management) ───────────────────────────────
-- Full project/task management. In multi-Presence mode, presence_id scopes
-- projects per user. In single mode (Cove agents), presence_id is NULL.

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

CREATE INDEX IF NOT EXISTS idx_projects_presence ON projects(presence_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_projects_slug_presence
    ON projects(slug, presence_id) WHERE presence_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS tasks (
    id               SERIAL PRIMARY KEY,
    project_id       INTEGER REFERENCES projects(id) ON DELETE CASCADE,
    presence_id      UUID,    -- owner (multi-mode); NULL = single-mode Cove (#191)
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
CREATE INDEX IF NOT EXISTS idx_tasks_presence ON tasks(presence_id);
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

-- ─── Contact Messages ──────────────────────────────────────────────────────

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

-- ── Approval requests (agent tool safety system) ────────────────────
-- Agents with @approve-tier tools propose actions here and wait for
-- operator confirmation before executing. Persisted so pending
-- requests survive container restarts.
CREATE TABLE IF NOT EXISTS approval_requests (
    id              SERIAL PRIMARY KEY,
    request_id      TEXT UNIQUE NOT NULL,
    tool_name       TEXT NOT NULL,
    description     TEXT DEFAULT '',
    args            JSONB DEFAULT '{}',
    tier            TEXT DEFAULT 'approve',
    status          TEXT DEFAULT 'pending',
    channel         TEXT DEFAULT '',
    result          TEXT,
    resolved_by     TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    resolved_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_approval_requests_status
    ON approval_requests(status, created_at DESC);

-- ─── Creation Actions (Creation Framework) ─────────────────────────────────
-- The Creation Framework applies the Lucid Principles mechanism to how work
-- gets done. Each action moves through stages: Broadcast → Tune → Act →
-- Receive → Manifest → Complete. Optional layer on top of standard tasks.

CREATE TABLE IF NOT EXISTS creation_actions (
    id              SERIAL PRIMARY KEY,
    -- Identity
    title           TEXT NOT NULL,
    intention       TEXT,                    -- what is being created
    frequency       TEXT,                    -- one of 13 broadcast frequencies
    tuning_key      TEXT,                    -- canon phrase anchor
    -- Stage tracking
    stage           TEXT DEFAULT 'broadcast', -- broadcast, tune, act, receive, manifest, complete
    -- Structured notes per stage
    tuning_notes    JSONB DEFAULT '{}',      -- {reflection, attention, imagination, clarity}
    signs_log       JSONB DEFAULT '[]',      -- [{text, logged_at}]
    manifest_notes  JSONB DEFAULT '{}',      -- {alignment, signs_review, frequency_shift, gratitude}
    -- Linkage
    project_id      INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    -- Metadata
    created_by      TEXT DEFAULT 'operator',
    archived        BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_creation_active
    ON creation_actions(archived, stage) WHERE archived = FALSE;
CREATE INDEX IF NOT EXISTS idx_creation_stage
    ON creation_actions(stage) WHERE archived = FALSE;


-- ============================================================================
-- Schema reconciliation (2026-06-22) — migration deltas folded into the base.
-- A fresh Cove runs ONLY this file; the numbered migrations are not auto-applied.
-- Everything below was previously migration-only and is required by current code.
-- All idempotent. Keep this in sync when a new schema migration is added:
-- if it must exist on a fresh Cove, fold it here too.
-- ============================================================================

-- ── accounts: per-operator extras (014_account_timezone, add-matrix-creds) ──
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS timezone VARCHAR(50);
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS matrix_username TEXT;
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS matrix_password TEXT;

-- ── tasks: accountability layer (accountability-layer.sql) ──
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'internal';
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS expected_by TIMESTAMPTZ;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS escalation_count INTEGER DEFAULT 0;
CREATE INDEX IF NOT EXISTS idx_tasks_source ON tasks(source) WHERE source = 'operator';
CREATE INDEX IF NOT EXISTS idx_tasks_expected_by ON tasks(expected_by) WHERE expected_by IS NOT NULL;

CREATE TABLE IF NOT EXISTS accountability_log (
    id              SERIAL PRIMARY KEY,
    sweep_at        TIMESTAMPTZ DEFAULT NOW(),
    tasks_checked   INTEGER NOT NULL,
    issues_found    INTEGER NOT NULL DEFAULT 0,
    escalations     JSONB DEFAULT '[]',
    notes           TEXT
);
CREATE INDEX IF NOT EXISTS idx_accountability_log_time ON accountability_log(sweep_at DESC);

-- ── youtube_queue (002_youtube_queue + 003 draft status) ──
CREATE TABLE IF NOT EXISTS youtube_queue (
    id              SERIAL PRIMARY KEY,
    title           TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    tags            JSONB NOT NULL DEFAULT '[]',
    hashtags        TEXT NOT NULL DEFAULT '',
    file_path       TEXT NOT NULL,
    category_id     TEXT NOT NULL DEFAULT '22',
    made_for_kids   BOOLEAN NOT NULL DEFAULT FALSE,
    is_short        BOOLEAN NOT NULL DEFAULT FALSE,
    related_video   TEXT,
    playlist_id     TEXT,
    thumbnail_path  TEXT,
    upload_date     TIMESTAMPTZ NOT NULL,
    publish_date    TIMESTAMPTZ NOT NULL,
    status          TEXT NOT NULL DEFAULT 'queued'
                    CHECK (status IN ('draft','queued','uploading','uploaded','published','failed','cancelled')),
    error_message   TEXT,
    youtube_video_id TEXT,
    youtube_url     TEXT,
    series          TEXT,
    card_id         TEXT,
    source_stem     TEXT,                -- #VP-SESS1 session key (master stem)
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    uploaded_at     TIMESTAMPTZ,
    published_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_ytq_upload_ready
    ON youtube_queue (upload_date) WHERE status = 'queued';
CREATE INDEX IF NOT EXISTS idx_ytq_publish_date
    ON youtube_queue (publish_date) WHERE status IN ('queued', 'uploading', 'uploaded');
CREATE INDEX IF NOT EXISTS idx_ytq_source_stem
    ON youtube_queue (source_stem)
    WHERE source_stem IS NOT NULL AND source_stem <> '';

-- ── social_queue (004_social_queue + 005 format column) ──
CREATE TABLE IF NOT EXISTS social_queue (
    id              SERIAL PRIMARY KEY,
    platform        TEXT NOT NULL DEFAULT 'youtube'
                    CHECK (platform IN ('youtube', 'tiktok', 'x', 'instagram', 'facebook')),
    title           TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    tags            JSONB NOT NULL DEFAULT '[]',
    hashtags        TEXT NOT NULL DEFAULT '',
    file_path       TEXT NOT NULL,
    preview_path    TEXT,
    thumbnail_path  TEXT,
    source_stem     TEXT,
    moment_id       INTEGER,
    clip_type       TEXT,
    clip_label      TEXT,
    duration_seconds REAL,
    is_vertical     BOOLEAN NOT NULL DEFAULT TRUE,
    upload_date     TIMESTAMPTZ,
    publish_date    TIMESTAMPTZ,
    status          TEXT NOT NULL DEFAULT 'draft'
                    CHECK (status IN ('draft','queued','uploading','uploaded','published','failed','cancelled')),
    error_message   TEXT,
    platform_data   JSONB NOT NULL DEFAULT '{}',
    series          TEXT,
    format          TEXT NOT NULL DEFAULT 'vertical'
                    CHECK (format IN ('vertical', 'horizontal', 'square')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    uploaded_at     TIMESTAMPTZ,
    published_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_sq_drafts
    ON social_queue (platform, status) WHERE status = 'draft';
CREATE INDEX IF NOT EXISTS idx_sq_upload_ready
    ON social_queue (upload_date) WHERE status = 'queued';
CREATE INDEX IF NOT EXISTS idx_sq_source
    ON social_queue (source_stem);

-- Shared updated_at trigger for the content queues.
CREATE OR REPLACE FUNCTION update_queue_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_ytq_updated ON youtube_queue;
CREATE TRIGGER trg_ytq_updated
    BEFORE UPDATE ON youtube_queue
    FOR EACH ROW EXECUTE FUNCTION update_queue_timestamp();

DROP TRIGGER IF EXISTS trg_sq_updated ON social_queue;
CREATE TRIGGER trg_sq_updated
    BEFORE UPDATE ON social_queue
    FOR EACH ROW EXECUTE FUNCTION update_queue_timestamp();

-- =============================================================================
-- cove_matrix — Connect/Matrix Space ownership for this Cove (#137 Phase A).
-- Single row (id=1). Holds the steward Matrix identity that OWNS the Cove Space,
-- plus the Space + Family room ids (the idempotency anchor for ensure_cove_space).
-- =============================================================================
CREATE TABLE IF NOT EXISTS cove_matrix (
    id               INTEGER PRIMARY KEY DEFAULT 1,
    steward_username TEXT,
    steward_password TEXT,
    space_id         TEXT,
    family_room_id   TEXT,
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    updated_at       TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT cove_matrix_singleton CHECK (id = 1)
);

-- =============================================================================
-- Hub network registrar (#133) — the global source of truth, replaces the
-- hand-edited network.yaml. These tables are only USED on the registry master
-- (the shared app / hub, LP_REGISTRY_MASTER); they sit empty + harmless on a
-- normal Cove, same as cove_matrix. Global uniqueness: cove name + @handle.
-- Canonical identity (#163): the registry handle is durable; a Matrix account is
-- a per-Cove projection of it (matrix_user below).
-- =============================================================================
CREATE TABLE IF NOT EXISTS registry_coves (
    cove_id       TEXT PRIMARY KEY,
    name          TEXT UNIQUE NOT NULL,        -- global Cove-name uniqueness
    owner_handle  TEXT,                         -- founding operator @handle
    domain        TEXT,                         -- e.g. smith.lucidcove.org
    homeserver    TEXT,                         -- matrix.{domain} (federation server_name)
    space_id      TEXT,                         -- the Cove's steward-owned Space (for nesting)
    mesh_ip       TEXT,                         -- Tailscale/Headscale IP of the host
    owner_token_hash TEXT,                      -- sha256 of the operator token that owns this claim (#4/#200)
    last_seen     TIMESTAMPTZ,                  -- heartbeat for ~30-day reclamation (#161)
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS registry_handles (
    handle        TEXT PRIMARY KEY,             -- globally unique @handle (canonical identity)
    cove_id       TEXT REFERENCES registry_coves(cove_id) ON DELETE SET NULL,
    matrix_user   TEXT,                         -- @handle:matrix.{cove}.{domain} projection
    referred_by   TEXT,                         -- the @handle that recruited this one (affiliate edge, #169)
    owner_token_hash TEXT,                      -- sha256 of the operator token that owns this claim (#4/#200)
    last_seen     TIMESTAMPTZ,                  -- heartbeat for ~30-day reclamation (#161)
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS registry_havens (
    haven_id      TEXT PRIMARY KEY,
    name          TEXT UNIQUE NOT NULL,
    owner_handle  TEXT,
    space_id      TEXT,                          -- the Haven Space (m.space)
    commons_id    TEXT,                          -- the Haven Commons room
    members       JSONB DEFAULT '[]'::jsonb,     -- federated member @handles
    member_coves  JSONB DEFAULT '[]'::jsonb,     -- [{cove_id, space_id, homeserver}]
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- cove_haven (#160) — Haven Spaces OWNED by an operator on THIS Cove. Mirrors
-- cove_matrix but Haven-level and keyed by haven_id (an operator can own more
-- than one). The idempotency anchor for ensure_haven_space.
-- =============================================================================
CREATE TABLE IF NOT EXISTS cove_haven (
    haven_id       TEXT PRIMARY KEY,
    name           TEXT,
    owner_user     TEXT,                         -- the founding operator's Matrix @user (the human owner)
    space_id       TEXT,
    commons_id     TEXT,
    steward_username TEXT,                        -- durable Haven steward that OWNS the Space/Commons (§2)
    steward_password TEXT,
    created_at     TIMESTAMPTZ DEFAULT NOW(),
    updated_at     TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- Credit economy (#128) — the internal credit ledger. HUB-only (the registry
-- master populates these; harmless empty tables on a self-host Cove, same as the
-- registry tables above). DOUBLE-ENTRY: every economic event writes >= 2
-- ledger_entries whose deltas net to zero. wallets.balance is a denormalized fast
-- read kept in lockstep with the entries (entries are the source of truth and
-- reconcile to it). External money (Stripe) only touches topup + payout; all
-- marketplace settlement is internal credit movement.
-- Credit unit: 1 credit = $0.01 (integer credits, BIGINT — never float).
-- Spec: LP-Vault/Reference/commerce-credit-economy-spec.md §4.
-- =============================================================================
CREATE TABLE IF NOT EXISTS wallets (
    id            SERIAL PRIMARY KEY,
    owner_handle  TEXT UNIQUE NOT NULL,         -- registry @handle, or a system wallet (_lp_fees, _issued)
    kind          TEXT NOT NULL DEFAULT 'member',  -- member | system
    balance       BIGINT NOT NULL DEFAULT 0,    -- integer credits; = SUM(ledger_entries.delta)
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS txns (
    id              TEXT PRIMARY KEY,           -- uuid4
    type            TEXT NOT NULL,              -- topup | purchase | payout | refund | adjustment
    source_handle   TEXT,                       -- payer/buyer (NULL for an external topup source)
    related_handle  TEXT,                       -- seller/payee
    listing_id      TEXT,
    gross           BIGINT NOT NULL DEFAULT 0,  -- the headline amount in credits
    status          TEXT NOT NULL DEFAULT 'posted',  -- posted | pending | reversed
    external_ref    TEXT,                       -- Stripe charge/payout id, etc.
    idempotency_key TEXT UNIQUE,                -- replay guard (webhook retries never double-post)
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ledger_entries (
    id            BIGSERIAL PRIMARY KEY,
    txn_id        TEXT NOT NULL REFERENCES txns(id) ON DELETE RESTRICT,
    wallet_id     INTEGER NOT NULL REFERENCES wallets(id) ON DELETE RESTRICT,
    delta         BIGINT NOT NULL,             -- +credit / -debit, integer credits
    kind          TEXT NOT NULL,               -- topup|purchase|platform_fee|affiliate_l1|affiliate_l2|payout|refund|adjustment|breakage
    ref_type      TEXT,
    ref_id        TEXT,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ledger_entries_wallet ON ledger_entries(wallet_id);
CREATE INDEX IF NOT EXISTS idx_ledger_entries_txn ON ledger_entries(txn_id);

-- =============================================================================
-- Presence profiles (#169) — the public presentation of a Presence (Operator +
-- Agent), keyed by registry @handle. Extends the identity already in `accounts`
-- (display_name, agent_name, agent_identity{archetype,frequency,tuning_key}) with
-- the presentation extras: avatars, a bio, and a templated skills set for the
-- searchable/matchable marketplace. Handle-keyed, so it serves a human Operator OR
-- an agent (agents are first-class economic actors). HUB-owned alongside the registry.
-- =============================================================================
CREATE TABLE IF NOT EXISTS presence_profiles (
    handle            TEXT PRIMARY KEY,          -- registry @handle (operator or agent)
    avatar_url        TEXT,                       -- the human Operator's photo
    agent_avatar_url  TEXT,                       -- the Agent's avatar
    bio               TEXT,
    skills            JSONB DEFAULT '[]'::jsonb,  -- templated tags + free tags
    links             JSONB DEFAULT '{}'::jsonb,  -- {site, social, ...}
    updated_at        TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- Profile mirror (#173) — the hub's cross-Cove copy of every Presence's PUBLIC
-- profile, keyed by @handle. accounts + presence_profiles are per-instance, so the
-- hub can't resolve a seller who lives on another Cove. Each instance pushes its
-- presences here (best-effort) on profile save / avatar / first listing, so a seller
-- on Cove B is viewable + searchable from Cove A (and the hub). Public data only.
-- =============================================================================
CREATE TABLE IF NOT EXISTS profile_mirror (
    handle            TEXT PRIMARY KEY,
    display_name      TEXT,
    agent_name        TEXT,
    cove              TEXT,
    archetype         TEXT,
    frequency         TEXT,
    tuning_key        TEXT,
    nickname          TEXT,
    avatar_url        TEXT,
    agent_avatar_url  TEXT,
    bio               TEXT,
    skills            JSONB DEFAULT '[]'::jsonb,
    links             JSONB DEFAULT '{}'::jsonb,
    updated_at        TIMESTAMPTZ DEFAULT NOW()
);
