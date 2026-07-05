-- Migration 016: JouleWork dollar cost (#183 build #1)
-- Adds cost_usd to jw_metrics. Populated by pricing.estimate_llm_cost() at
-- write time. NULL = unpriced model (we don't guess); local ollama = 0.0.
-- Idempotent. Auto-applied by the entrypoint (#171).

ALTER TABLE jw_metrics ADD COLUMN IF NOT EXISTS cost_usd NUMERIC(12,6);

CREATE INDEX IF NOT EXISTS idx_jw_cost
    ON jw_metrics(agent_id, recorded_at DESC) WHERE cost_usd IS NOT NULL;
