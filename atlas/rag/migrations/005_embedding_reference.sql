-- 005_embedding_reference.sql — Embedding Drift Reference Corpus
-- Stores 1000 diverse trade analyses with their original embeddings
-- for real-time embedding model drift detection (Phase 8).

CREATE TABLE IF NOT EXISTS embedding_reference (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_signal_id TEXT NOT NULL,
    asset           TEXT NOT NULL,
    outcome_label   TEXT,
    score           INTEGER NOT NULL,
    content_hash    TEXT NOT NULL UNIQUE,
    content_text    TEXT NOT NULL,
    embedding       vector(1024) NOT NULL,
    model_version   TEXT NOT NULL DEFAULT 'mistral-embed',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- B-tree indexes for stratification queries and deduplication.
CREATE INDEX IF NOT EXISTS idx_embedding_ref_asset
    ON embedding_reference (asset);

CREATE INDEX IF NOT EXISTS idx_embedding_ref_hash
    ON embedding_reference (content_hash);

CREATE INDEX IF NOT EXISTS idx_embedding_ref_score
    ON embedding_reference (score);

-- Track migration application.
INSERT INTO rag_migrations (migration_id)
VALUES ('005_embedding_reference')
ON CONFLICT DO NOTHING;
