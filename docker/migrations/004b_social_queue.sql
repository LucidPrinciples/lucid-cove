-- Migration 004: social_queue — Multi-platform content distribution queue.
--
-- Generalizes youtube_queue for all platforms. Each processed moment gets
-- one row per target platform. The operator reviews, edits metadata, and
-- schedules from the Actions tab. Stuart (or platform-specific agents)
-- handle the actual upload when the scheduled time arrives.
--
-- Platforms: youtube, tiktok, x, instagram, facebook
-- Status flow: draft → queued → uploading → uploaded → published → failed/cancelled
--
-- youtube_queue is left as-is for backward compatibility. New content
-- goes into social_queue. The upload system reads from here.

CREATE TABLE IF NOT EXISTS social_queue (
    id              SERIAL PRIMARY KEY,

    -- Platform
    platform        TEXT NOT NULL DEFAULT 'youtube'
                    CHECK (platform IN ('youtube', 'tiktok', 'x', 'instagram', 'facebook')),

    -- Content metadata (operator edits these before scheduling)
    title           TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    tags            JSONB NOT NULL DEFAULT '[]',
    hashtags        TEXT NOT NULL DEFAULT '',

    -- Video file references
    file_path       TEXT NOT NULL,                       -- full-res clip (NC path)
    preview_path    TEXT,                                -- low-res preview (NC path)
    thumbnail_path  TEXT,                                -- custom thumbnail (NC path)

    -- Source tracking — links back to the video pipeline
    source_stem     TEXT,                                -- e.g. 'IMG_7129'
    moment_id       INTEGER,                             -- moment number from analysis
    clip_type       TEXT,                                -- quote, thought, story
    clip_label      TEXT,                                -- original label from analysis
    duration_seconds REAL,                               -- clip duration
    is_vertical     BOOLEAN NOT NULL DEFAULT TRUE,       -- 9:16 vs 16:9

    -- Scheduling
    upload_date     TIMESTAMPTZ,                         -- when to upload (null = unscheduled)
    publish_date    TIMESTAMPTZ,                         -- when to publish (null = immediate)

    -- Status tracking
    status          TEXT NOT NULL DEFAULT 'draft'
                    CHECK (status IN ('draft','queued','uploading','uploaded','published','failed','cancelled')),
    error_message   TEXT,

    -- Platform-specific data (youtube_video_id, tiktok_post_id, etc.)
    platform_data   JSONB NOT NULL DEFAULT '{}',

    -- Series / grouping
    series          TEXT,                                -- e.g. 'hltb', 'build-journey'

    -- Timestamps
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    uploaded_at     TIMESTAMPTZ,
    published_at    TIMESTAMPTZ
);

-- Find drafts ready for review
CREATE INDEX IF NOT EXISTS idx_sq_drafts
    ON social_queue (platform, status)
    WHERE status = 'draft';

-- Find queued items ready for upload
CREATE INDEX IF NOT EXISTS idx_sq_upload_ready
    ON social_queue (upload_date)
    WHERE status = 'queued';

-- Find by source stem (all clips from one video)
CREATE INDEX IF NOT EXISTS idx_sq_source
    ON social_queue (source_stem);

-- Auto-update timestamp
CREATE OR REPLACE FUNCTION update_social_queue_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_sq_updated ON social_queue;
CREATE TRIGGER trg_sq_updated
    BEFORE UPDATE ON social_queue
    FOR EACH ROW
    EXECUTE FUNCTION update_social_queue_timestamp();
