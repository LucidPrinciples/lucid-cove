-- Migration: Add knowledge_base table for vector semantic search
-- Run on both Stuart and Atlas DBs
-- Date: 2026-05-09

-- Extension already exists (in init-base.sql) but safe to repeat
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS knowledge_base (
    id              SERIAL PRIMARY KEY,
    doc_name        TEXT NOT NULL,
    chunk_index     INTEGER NOT NULL,
    chunk_text      TEXT NOT NULL,
    embedding       vector(768),
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(doc_name, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_kb_embedding ON knowledge_base
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
