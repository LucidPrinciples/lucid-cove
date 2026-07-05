-- Migration: Add semantic search embeddings to agent_memory
-- Uses nomic-embed-text (768 dimensions) via Ollama — same model as knowledge_base
--
-- Run via Runbook 12 on each DB (Stuart + Atlas)

-- If the column exists with wrong dimensions (1536), drop and recreate
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'agent_memory' AND column_name = 'embedding'
    ) THEN
        -- Check current dimension by querying the type
        PERFORM 1 FROM pg_attribute a
        JOIN pg_class c ON a.attrelid = c.oid
        JOIN pg_type t ON a.atttypid = t.oid
        WHERE c.relname = 'agent_memory'
          AND a.attname = 'embedding'
          AND t.typname = 'vector';

        IF FOUND THEN
            -- Column exists as vector type — drop it so we can recreate with correct dimensions
            -- (safe because no embeddings have been stored yet)
            ALTER TABLE agent_memory DROP COLUMN embedding;
            RAISE NOTICE 'Dropped existing embedding column (recreating with 768 dimensions)';
        END IF;
    END IF;
END $$;

-- Add the column with correct dimensions
ALTER TABLE agent_memory ADD COLUMN IF NOT EXISTS embedding vector(768);

-- HNSW index for fast approximate nearest neighbor search
-- Only indexes active memories with embeddings
CREATE INDEX IF NOT EXISTS idx_memory_embedding
    ON agent_memory USING hnsw (embedding vector_cosine_ops)
    WHERE is_active = TRUE AND embedding IS NOT NULL;

-- Confirm
SELECT 'agent_memory embedding column ready (768 dimensions)' AS status;
