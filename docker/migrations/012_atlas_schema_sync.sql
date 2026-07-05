-- =============================================================================
-- 012_atlas_schema_sync.sql — Sync Atlas (and any agent) to full init-base.sql
-- =============================================================================
-- Tables that existed in init-base.sql but were never created on Atlas because
-- Atlas's DB was initialized before these tables were added to init-base.sql.
-- All statements are IF NOT EXISTS / IF NOT EXISTS safe — idempotent.
-- =============================================================================

-- ─── Accounts ───────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS accounts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    display_name    TEXT NOT NULL,
    username        TEXT,
    email           TEXT,
    agent_name      TEXT,
    last_name       TEXT DEFAULT '',
    tier            TEXT NOT NULL DEFAULT 'free',
    cove_role       TEXT DEFAULT 'member',
    cove_id         TEXT,
    agent_config    JSONB DEFAULT '{}',
    active_workflows TEXT[] DEFAULT '{}',
    api_mode        TEXT DEFAULT 'cove',
    name_locked     BOOLEAN DEFAULT FALSE,
    auth_token      TEXT NOT NULL,
    active          BOOLEAN DEFAULT TRUE,
    preferences     JSONB DEFAULT '{}',
    stripe_customer_id TEXT,
    referral_code   TEXT,
    referred_by     UUID,
    nc_username     VARCHAR(100),
    nc_password     TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    last_access     TIMESTAMPTZ,
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_username
    ON accounts(username) WHERE username IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_email
    ON accounts(email) WHERE email IS NOT NULL;
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

-- ─── Auth Sessions ──────────────────────────────────────────────────────────

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

-- ─── Tuning Sessions ────────────────────────────────────────────────────────

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

-- ─── Tuning Events ──────────────────────────────────────────────────────────

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

-- ─── Tuning Favorites ───────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS tuning_favorites (
    id                  SERIAL PRIMARY KEY,
    presence_id         UUID REFERENCES accounts(id) UNIQUE,
    legacy_user_id      TEXT,
    favorites_json      JSONB DEFAULT '[]',
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tf_presence ON tuning_favorites(presence_id);

-- ─── Tuning Streaks ─────────────────────────────────────────────────────────

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

-- ─── Contact Messages ───────────────────────────────────────────────────────

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

-- ─── Quick List Activity ────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS quick_list_activity (
    id              SERIAL PRIMARY KEY,
    list_id         INTEGER NOT NULL REFERENCES quick_lists(id) ON DELETE CASCADE,
    item_id         INTEGER REFERENCES quick_list_items(id) ON DELETE SET NULL,
    presence_id     UUID,
    action          TEXT NOT NULL,
    detail          TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_qla_list ON quick_list_activity(list_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_qla_presence ON quick_list_activity(presence_id);

-- ─── Quick Lists — add archived columns if missing ──────────────────────────

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'quick_lists' AND column_name = 'archived') THEN
        ALTER TABLE quick_lists ADD COLUMN archived BOOLEAN DEFAULT FALSE;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'quick_lists' AND column_name = 'archived_at') THEN
        ALTER TABLE quick_lists ADD COLUMN archived_at TIMESTAMPTZ;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'quick_list_items' AND column_name = 'archived') THEN
        ALTER TABLE quick_list_items ADD COLUMN archived BOOLEAN DEFAULT FALSE;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'quick_list_items' AND column_name = 'archived_at') THEN
        ALTER TABLE quick_list_items ADD COLUMN archived_at TIMESTAMPTZ;
    END IF;
END $$;
