-- ============================================================================
-- Migration 006: Tuning Sessions — Human practice data
-- ============================================================================
-- Mirrors the Lucid Tuner D1 schema (sessions, events, user_favorites)
-- for clean import of existing user data into Postgres.
--
-- These tables are HUMAN tuning sessions (Field/Tune/Rebroadcast).
-- The existing `echoes` table holds AGENT tuning records (LTP pipeline).
-- Both coexist — different data, different purpose.
--
-- New columns not in D1 (Field Tuning enhancements):
--   signal_before, signal_after, journal_text, journal_at, list_captures
--
-- Apply: cat migrations/006_tuning_sessions.sql | psql ...
-- ============================================================================

-- ─── Tuning Sessions ─────────────────────────────────────────────────────────
-- One row per human tuning session. Primary table for Tuner/Operator/Presence.
-- Column names match D1 sessions table for import compatibility.

CREATE TABLE IF NOT EXISTS tuning_sessions (
    id                  SERIAL PRIMARY KEY,
    session_id          TEXT NOT NULL UNIQUE,
    -- User linkage: presence_id for Cove users, legacy_user_id for D1 imports
    presence_id         UUID REFERENCES presences(id),
    legacy_user_id      TEXT,               -- D1 user_id (preserved for import mapping)

    -- When
    date                TEXT,               -- YYYY-MM-DD
    time                TEXT,               -- HH:MM:SS
    day_of_week         TEXT,               -- Monday, Tuesday, etc.

    -- Entry mode
    entry_mode          TEXT,               -- 'Field', 'Tune', 'Rebroadcast'
    initial_state       TEXT,               -- Field term selected (e.g. 'trust', 'clarity')
    context             TEXT,               -- 'Driving', 'Working / Focus', etc.
    rebroadcast_of      TEXT,               -- session_id of original (for Rebroadcast)

    -- What was served
    principle_served    TEXT,               -- 'Peace', 'Clarity', etc.
    frequency_category  TEXT,               -- Same as principle_served (legacy compat)
    echo_filename       TEXT,               -- 'Peace_01.mp3'
    echo_album          TEXT,               -- Signal type album
    echo_full_name      TEXT,               -- Display name
    echo_signal_type    TEXT,               -- 'Ground_Signal', 'Clear_Signal', etc.
    tuning_key_primary  TEXT,               -- The tuning key quote
    bpm                 INTEGER,            -- Beats per minute

    -- Selection method
    quantum_selection   BOOLEAN DEFAULT FALSE,
    quantum_raw_value   TEXT,
    selection_method    TEXT,               -- 'crypto', 'pseudo', 'quantum'
    excluded_signal_types TEXT,             -- Comma-separated exclusions

    -- Love Equation
    e_start             REAL,              -- User-declared energy/openness (0-1)
    c_value             REAL,              -- Coherence after echo
    d_value             REAL,              -- Static/resistance
    beta_value          REAL,              -- Responsiveness coefficient
    de_dt               REAL,              -- Rate of change
    signal_direction    TEXT,              -- 'constructive' or 'destructive'

    -- Practice content (persisted from frontend assembly)
    insight_text        TEXT,              -- Coaching/intro text shown
    practice_html       TEXT,              -- Rendered practice HTML
    practice_steps_json TEXT,              -- JSON array of {title, text} steps

    -- Stage/diagnosis (from Flowise era, preserved for import)
    stage_diagnosed     TEXT,
    echo_delivered      TEXT,
    end_state           TEXT,

    -- Metadata
    user_tier           TEXT,              -- 'free', 'operator', 'presence', 'cove'
    source_platform     TEXT DEFAULT 'web', -- 'web', 'ios', 'android'
    tool_version        TEXT,

    -- ─── Field Tuning Enhancements (NEW — not in D1) ────────────────────────
    signal_before       SMALLINT,          -- 1-5 self-rated state before tuning
    signal_after        SMALLINT,          -- 1-5 self-rated state after tuning
    journal_text        TEXT,              -- Post-tuning reflection entry
    journal_at          TIMESTAMPTZ,       -- When journal was written

    -- Timestamps
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ts_presence ON tuning_sessions(presence_id);
CREATE INDEX IF NOT EXISTS idx_ts_legacy_user ON tuning_sessions(legacy_user_id);
CREATE INDEX IF NOT EXISTS idx_ts_date ON tuning_sessions(date);
CREATE INDEX IF NOT EXISTS idx_ts_session_id ON tuning_sessions(session_id);
CREATE INDEX IF NOT EXISTS idx_ts_presence_date ON tuning_sessions(presence_id, date DESC);


-- ─── Tuning Events ───────────────────────────────────────────────────────────
-- Granular event log. Mirrors D1 events table.
-- Tracks: playback events, session events, UI events, engagement.

CREATE TABLE IF NOT EXISTS tuning_events (
    id                  SERIAL PRIMARY KEY,
    presence_id         UUID REFERENCES presences(id),
    legacy_user_id      TEXT,

    event_type          TEXT NOT NULL,       -- echo_play_start, tuning_complete, daily_streak, etc.
    event_data          JSONB DEFAULT '{}',  -- Flexible payload (D1 stored as TEXT, we use JSONB)
    session_id          TEXT,                -- Links to tuning_sessions.session_id

    -- Denormalized for fast queries (mirrors D1 columns)
    echo_name           TEXT,
    echo_album          TEXT,
    principle           TEXT,
    frequency           TEXT,
    signal_type         TEXT,
    context             TEXT,
    bpm                 INTEGER,
    play_duration       REAL,               -- Seconds played
    position_in_playlist INTEGER,
    tuning_key          TEXT,
    play_source         TEXT,               -- 'click', 'auto_play', 'try_page'

    -- Selection
    quantum_selection   BOOLEAN DEFAULT FALSE,
    selection_method    TEXT,

    -- Metadata
    user_tier           TEXT,
    excluded_signal_types TEXT,
    source_platform     TEXT DEFAULT 'web',

    -- Timestamps
    timestamp           TIMESTAMPTZ DEFAULT NOW(),
    date                TEXT,               -- YYYY-MM-DD (for partitioning/queries)
    time                TEXT                -- HH:MM:SS
);

CREATE INDEX IF NOT EXISTS idx_te_presence ON tuning_events(presence_id);
CREATE INDEX IF NOT EXISTS idx_te_legacy_user ON tuning_events(legacy_user_id);
CREATE INDEX IF NOT EXISTS idx_te_date_type ON tuning_events(date, event_type);
CREATE INDEX IF NOT EXISTS idx_te_session ON tuning_events(session_id);


-- ─── Tuning Favorites ────────────────────────────────────────────────────────
-- Per-user favorite echoes. D1 stored as JSON blob; we normalize lightly.
-- Keep the JSON approach for now (matches D1), can normalize later if needed.

CREATE TABLE IF NOT EXISTS tuning_favorites (
    id                  SERIAL PRIMARY KEY,
    presence_id         UUID REFERENCES presences(id),
    legacy_user_id      TEXT,
    favorites_json      JSONB DEFAULT '[]', -- Array of {echo_filename, frequency, signal_type, ...}
    updated_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(presence_id)
);

CREATE INDEX IF NOT EXISTS idx_tf_presence ON tuning_favorites(presence_id);
CREATE INDEX IF NOT EXISTS idx_tf_legacy_user ON tuning_favorites(legacy_user_id);


-- ─── Tuning Streaks ──────────────────────────────────────────────────────────
-- Daily streak tracking. One row per user, updated each session.

CREATE TABLE IF NOT EXISTS tuning_streaks (
    id                  SERIAL PRIMARY KEY,
    presence_id         UUID REFERENCES presences(id) UNIQUE,
    legacy_user_id      TEXT,
    current_streak      INTEGER DEFAULT 0,  -- Consecutive days
    longest_streak      INTEGER DEFAULT 0,  -- All-time best
    last_tuning_date    TEXT,               -- YYYY-MM-DD of last completed session
    total_sessions      INTEGER DEFAULT 0,  -- Lifetime count
    this_month_sessions INTEGER DEFAULT 0,  -- Current month count
    last_month_reset    TEXT,               -- YYYY-MM when month counter was last reset
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tstreak_presence ON tuning_streaks(presence_id);


-- ─── User Preferences (tuning-specific) ──────────────────────────────────────
-- Tuning preferences that were on D1 users table. Kept separate from presences
-- to avoid polluting the auth/identity table with app-specific prefs.

CREATE TABLE IF NOT EXISTS tuning_preferences (
    id                      SERIAL PRIMARY KEY,
    presence_id             UUID REFERENCES presences(id) UNIQUE,
    legacy_user_id          TEXT,
    excluded_signal_types   TEXT DEFAULT '',  -- Comma-separated signal types to skip
    preferred_frequency     TEXT,             -- User's preferred frequency category
    top_frequency           TEXT,             -- Most-used frequency (computed)
    last_principle          TEXT,             -- Last principle served
    updated_at              TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tpref_presence ON tuning_preferences(presence_id);
