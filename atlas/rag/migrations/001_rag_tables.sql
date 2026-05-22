-- 001_rag_tables.sql — RAG Signal Memory Tables
-- Requires PostgreSQL 15+ with pgvector extension.
-- All embedding columns use vector(1024) for Mistral compatibility.

-- ─── EXTENSIONS ───────────────────────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ─── MIGRATION TRACKER ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS rag_migrations (
    migration_id   TEXT PRIMARY KEY,
    applied_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ─── SIGNAL HISTORY ──────────────────────────────────────────────────────────
-- Stores embedded summaries of every SignalOutput for retrieval-augmented
-- context. The embedding column holds a 1024-dimensional Mistral vector.
CREATE TABLE IF NOT EXISTS signal_history (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    signal_id       TEXT        NOT NULL UNIQUE,
    asset           TEXT        NOT NULL,
    timeframe       TEXT        NOT NULL DEFAULT '30m',
    decision        TEXT        NOT NULL,
    score           INTEGER     NOT NULL CHECK (score BETWEEN 0 AND 100),
    confidence      DOUBLE PRECISION NOT NULL CHECK (confidence BETWEEN 0.0 AND 1.0),
    raw_score       INTEGER     NOT NULL DEFAULT 0 CHECK (raw_score BETWEEN 0 AND 220),
    reasoning       TEXT        NOT NULL DEFAULT '',
    embedding       vector(1024) NOT NULL,
    -- Post-trade enrichment (written by write_outcome)
    pnl_pct         DOUBLE PRECISION,
    outcome_label   TEXT CHECK (outcome_label IN ('WIN', 'LOSS', 'SCRATCH', NULL)),
    outcome_at      TIMESTAMPTZ,
    -- Metadata
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata        JSONB       NOT NULL DEFAULT '{}'::jsonb
);

-- ─── PATTERN MEMORY ──────────────────────────────────────────────────────────
-- Stores curated lessons, post-mortems, and recurring patterns.
-- Each row is a human-or-system-authored pattern with its embedding.
CREATE TABLE IF NOT EXISTS pattern_memory (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    title           TEXT        NOT NULL,
    content         TEXT        NOT NULL,
    category        TEXT        NOT NULL DEFAULT 'general',
    embedding       vector(1024) NOT NULL,
    source          TEXT        NOT NULL DEFAULT 'system',
    relevance_score DOUBLE PRECISION NOT NULL DEFAULT 1.0 CHECK (relevance_score BETWEEN 0.0 AND 1.0),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata        JSONB       NOT NULL DEFAULT '{}'::jsonb
);

-- ─── INDEXES ─────────────────────────────────────────────────────────────────
-- HNSW indexes for approximate nearest-neighbour search on embeddings.
-- m=16 and ef_construction=128 balance recall vs. build time for ~100k rows.

CREATE INDEX IF NOT EXISTS idx_signal_history_embedding
    ON signal_history
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 128);

CREATE INDEX IF NOT EXISTS idx_pattern_memory_embedding
    ON pattern_memory
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 128);

-- B-tree indexes for common filter predicates.
CREATE INDEX IF NOT EXISTS idx_signal_history_asset     ON signal_history (asset);
CREATE INDEX IF NOT EXISTS idx_signal_history_created   ON signal_history (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_signal_history_decision  ON signal_history (decision);
CREATE INDEX IF NOT EXISTS idx_pattern_memory_category  ON pattern_memory (category);
CREATE INDEX IF NOT EXISTS idx_pattern_memory_created   ON pattern_memory (created_at DESC);
