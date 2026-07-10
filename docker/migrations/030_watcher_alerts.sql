-- 030: watcher_alerts — the background watcher's persistent alert surface.
-- The watcher (src/utils/watcher.py) runs on the host scheduler every 15 minutes,
-- checks cheap DB facts (failed approved tools, stale approvals, stuck queues,
-- missed tunings, pushes with no PR) and upserts findings here. Open alerts
-- render as Attention cards on the operator's home. Nothing fails silently.
--
-- Steward-unit spec Pillar 3 (LP-Vault/Projects/OSS-Flip-Reorg/steward-unit-spec.md).

CREATE TABLE IF NOT EXISTS watcher_alerts (
    id          SERIAL PRIMARY KEY,
    alert_key   TEXT UNIQUE NOT NULL,      -- stable per-condition key (dedup across runs)
    category    TEXT NOT NULL,             -- approved-failed | approval-stale | queue-stuck | tuning-missing | push-no-pr
    title       TEXT NOT NULL,
    detail      TEXT DEFAULT '',
    urgency     TEXT DEFAULT 'high',       -- high | normal
    status      TEXT NOT NULL DEFAULT 'open'
                CHECK (status IN ('open','resolved','dismissed')),
    first_seen  TIMESTAMPTZ DEFAULT NOW(),
    last_seen   TIMESTAMPTZ DEFAULT NOW(),
    resolved_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_watcher_alerts_status
    ON watcher_alerts (status, last_seen DESC);
