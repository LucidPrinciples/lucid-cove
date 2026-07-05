-- youtube_queue — Scheduled YouTube post queue.
-- Posts are saved here from the action page, then Stuart's job runner
-- uploads them to YouTube when upload_date arrives.
--
-- Two-layer scheduling:
--   1. upload_date  = when Stuart sends the video to YouTube (as private + scheduled)
--   2. publish_date = when YouTube flips it to public (publish_at in API)
--
-- After upload, Stuart generates follow-up tasks for Studio-only actions
-- (connect related video, set altered content label, etc.)

CREATE TABLE IF NOT EXISTS youtube_queue (
    id              SERIAL PRIMARY KEY,

    -- Video metadata (from action page card fields)
    title           TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    tags            JSONB NOT NULL DEFAULT '[]',        -- array of tag strings
    hashtags        TEXT NOT NULL DEFAULT '',            -- stored separately, appended to desc on upload
    file_path       TEXT NOT NULL,                       -- relative to /content root
    category_id     TEXT NOT NULL DEFAULT '22',          -- 22=People & Blogs
    made_for_kids   BOOLEAN NOT NULL DEFAULT FALSE,
    is_short        BOOLEAN NOT NULL DEFAULT FALSE,

    -- Related content (for manual Studio follow-up tasks)
    related_video   TEXT,                                -- long-form video title/URL to link on the short
    playlist_id     TEXT,                                -- YouTube playlist to add to after upload
    thumbnail_path  TEXT,                                -- relative to /content, custom thumbnail image

    -- Scheduling
    upload_date     TIMESTAMPTZ NOT NULL,                -- when Stuart uploads to YouTube
    publish_date    TIMESTAMPTZ NOT NULL,                -- when YouTube makes it public (publish_at)

    -- Status tracking
    status          TEXT NOT NULL DEFAULT 'queued'
                    CHECK (status IN ('queued','uploading','uploaded','published','failed','cancelled')),
    error_message   TEXT,                                -- populated on failure
    youtube_video_id TEXT,                               -- populated after successful upload
    youtube_url     TEXT,                                -- populated after successful upload

    -- Series / grouping (for calendar view)
    series          TEXT,                                -- e.g. 'hltb', 'hltagb', 'ras'
    card_id         TEXT,                                -- original card id from action page (e.g. 'post-1')

    -- Timestamps
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    uploaded_at     TIMESTAMPTZ,                         -- when Stuart actually uploaded
    published_at    TIMESTAMPTZ                          -- when YouTube confirmed public
);

-- Index for the job runner: find queued posts ready for upload
CREATE INDEX IF NOT EXISTS idx_ytq_upload_ready
    ON youtube_queue (upload_date)
    WHERE status = 'queued';

-- Index for calendar/dashboard views
CREATE INDEX IF NOT EXISTS idx_ytq_publish_date
    ON youtube_queue (publish_date)
    WHERE status IN ('queued', 'uploading', 'uploaded');

-- Auto-update updated_at on any change
CREATE OR REPLACE FUNCTION update_youtube_queue_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_ytq_updated ON youtube_queue;
CREATE TRIGGER trg_ytq_updated
    BEFORE UPDATE ON youtube_queue
    FOR EACH ROW
    EXECUTE FUNCTION update_youtube_queue_timestamp();
