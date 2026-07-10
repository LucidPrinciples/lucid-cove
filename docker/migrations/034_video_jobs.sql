-- 034: video_jobs — durable state for the async video-pipeline job registry (#D39).
--
-- The registry in src/dashboard/routes/video_jobs.py was IN-MEMORY only: an app
-- restart silently dropped every in-flight job, so a render that FINISHED in
-- pipecat still showed the browser an error at the end (its job_id 404'd once the
-- process restarted). This table mirrors each job's lightweight STATE (not the
-- result payload). On boot we orphan-mark any still queued/running row to
-- 'failed' with an honest error, so a polling browser gets the truth instead of
-- a 404 or an eternal spinner — the same shape #D30 gave delegated tasks.
--
-- Timestamps are stored as epoch DOUBLE PRECISION to match the in-memory job
-- shape (time.time()), so the DB-fallback path returns identical fields.

CREATE TABLE IF NOT EXISTS video_jobs (
    job_id      TEXT PRIMARY KEY,
    kind        TEXT DEFAULT '',
    state       TEXT NOT NULL DEFAULT 'queued',   -- queued | running | done | failed
    phase       TEXT DEFAULT 'queued',
    error       TEXT DEFAULT '',
    created_at  DOUBLE PRECISION,
    started_at  DOUBLE PRECISION,
    finished_at DOUBLE PRECISION,
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_video_jobs_state
    ON video_jobs (state, updated_at DESC);
