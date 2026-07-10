-- 031: steward_queue — the steward's Cove-level EXECUTION queue.
-- Steward-unit spec Pillar 1 (DB-backed, decided 2026-07-10).
--
-- Intake stays the operator's: the jules backlog board is a PRE-SORT inbox,
-- kept close to empty. When the operator says "team takes this" (the → Team
-- button on a board item), the item flows OUT of the board's world and INTO
-- this queue, which the steward owns: takes tickets, assigns, tracks the PR,
-- marks done AFTER the operator merges + deploys. Intake board ≠ execution
-- queue. The watcher monitors for assigned-but-untouched staleness.

CREATE TABLE IF NOT EXISTS steward_queue (
    id          SERIAL PRIMARY KEY,
    source      TEXT DEFAULT '',           -- board ref, e.g. '#D16' or a jules id
    title       TEXT NOT NULL,
    detail      TEXT DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'queued'
                CHECK (status IN ('queued','assigned','in_review','done','dropped')),
    assignee    TEXT DEFAULT '',           -- steward or team agent id
    pr_url      TEXT DEFAULT '',           -- set when a PR exists
    notes       TEXT DEFAULT '',           -- running steward notes
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW(),
    done_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_steward_queue_status
    ON steward_queue (status, updated_at DESC);
