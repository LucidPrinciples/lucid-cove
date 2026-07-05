-- 024: system_settings — the per-Cove key/value store settings.py has ALWAYS
-- assumed exists, but no migration ever created (it was hand-created on the
-- founder/Clearfield, so every FRESH install boots without it: the settings
-- cache falls back to _DEFAULTS and the wizard-finalize family_name mirror
-- silently no-ops — the "Stuart Cove" team-showcase bug, CF-89's third leg).
-- Idempotent; matches src/utils/settings.py's reads/UPSERT exactly.

CREATE TABLE IF NOT EXISTS system_settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL DEFAULT '',
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
