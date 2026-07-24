-- 040: #VP-SESS1 — session identity on youtube_queue
-- social_queue already has source_stem (master stem). Promote-to-YouTube
-- and scheduled/history surfaces need the same key so full + N shorts
-- group as one session across both queues.

ALTER TABLE youtube_queue ADD COLUMN IF NOT EXISTS source_stem TEXT;

CREATE INDEX IF NOT EXISTS idx_ytq_source_stem
    ON youtube_queue (source_stem)
    WHERE source_stem IS NOT NULL AND source_stem <> '';

-- Best-effort backfill from series "moments-{stem}" (pipeline default).
UPDATE youtube_queue
   SET source_stem = substring(series FROM '^moments-(.+)$')
 WHERE (source_stem IS NULL OR source_stem = '')
   AND series ~ '^moments-.+';

-- file_path basename hints: IMG_1234..., stem-captioned, etc.
UPDATE youtube_queue
   SET source_stem = upper(substring(
        regexp_replace(file_path, '^.*/', '')
        FROM '(IMG_[0-9]+)'
   ))
 WHERE (source_stem IS NULL OR source_stem = '')
   AND file_path ~* 'IMG_[0-9]+';
