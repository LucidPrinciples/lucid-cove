-- =============================================================================
-- Migration 004: Review Reports (Accountability Layers 2 + 3)
-- =============================================================================
-- Stores peer review reports and Vera's meta-reviews from the nightly cycle.
--
-- Run via Runbook 12 on each agent DB that needs it:
--   cat docker/migrations/004_review_reports.sql | ssh ... "docker exec -i {container}-postgres psql -U {user} -d {db}"
-- =============================================================================

CREATE TABLE IF NOT EXISTS review_reports (
    id              SERIAL PRIMARY KEY,
    review_type     TEXT NOT NULL,           -- 'peer' or 'meta'
    frequency       TEXT NOT NULL,           -- day's frequency when review ran
    reviewer_id     TEXT NOT NULL,           -- who wrote this review
    target_id       TEXT NOT NULL,           -- who was reviewed ('all' for meta)
    report_data     JSONB NOT NULL,          -- full structured review report
    reviewed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Query patterns: by date, by reviewer, by target, meta-reviews only
CREATE INDEX IF NOT EXISTS idx_review_date
    ON review_reports(reviewed_at DESC);
CREATE INDEX IF NOT EXISTS idx_review_type_date
    ON review_reports(review_type, reviewed_at DESC);
CREATE INDEX IF NOT EXISTS idx_review_target
    ON review_reports(target_id, reviewed_at DESC);
CREATE INDEX IF NOT EXISTS idx_review_reviewer
    ON review_reports(reviewer_id, reviewed_at DESC);
