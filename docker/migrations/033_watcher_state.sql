-- 033: watcher_state — tiny key/value scratch for the background watcher (#D33).
--
-- The infra-drift checks need to remember one value between sweeps (the last-seen
-- mtime of the Caddy proxy config) so they can tell "changed since last sweep" from
-- "same as always". watcher_alerts is keyed per-condition with no value column, so a
-- purpose-built state row is cleaner than overloading an alert. Read/written only by
-- src/utils/watcher.py; safe to be empty (checks no-op when a key is missing).

CREATE TABLE IF NOT EXISTS watcher_state (
    key         TEXT PRIMARY KEY,
    value       TEXT,
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);
