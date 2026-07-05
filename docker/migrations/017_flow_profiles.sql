-- Migration 017: Flow profiles (#183 build #1, part c)
-- Per-flow, per-step expected resource use BY KIND (LLM tokens / ASR minutes /
-- GPU minutes), as a self-updating rolling average. Feeds the pre-flight
-- estimator + per-run chooser. Seeded from jw_metrics history + video
-- durations, then updated incrementally as real runs accrue.
-- Idempotent. Auto-applied by the entrypoint (#171).

CREATE TABLE IF NOT EXISTS flow_profiles (
    id            SERIAL PRIMARY KEY,
    flow          TEXT NOT NULL,                 -- e.g. 'ltp-morning', 'video-pipeline'
    step          TEXT NOT NULL DEFAULT '*',     -- step within the flow ('*' = whole flow)
    unit_kind     TEXT NOT NULL,                 -- 'llm_tokens' | 'asr_minutes' | 'gpu_minutes'
    avg_units     DOUBLE PRECISION NOT NULL DEFAULT 0,
    sample_count  INTEGER NOT NULL DEFAULT 0,
    last_units    DOUBLE PRECISION,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (flow, step, unit_kind)
);

CREATE INDEX IF NOT EXISTS idx_flow_profiles_flow ON flow_profiles(flow);
