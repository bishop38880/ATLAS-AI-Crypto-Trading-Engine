-- 003_agent_zero.sql — Add archive columns to signal_history for Agent Zero
-- Session 24: Memory Lifecycle & Garbage Collection

ALTER TABLE signal_history
    ADD COLUMN IF NOT EXISTS archived    BOOLEAN     DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS escore      NUMERIC(6,4) DEFAULT NULL;

-- Partial index for fast active-only queries (Agent Zero + RAG retrieval)
CREATE INDEX IF NOT EXISTS idx_active_signal_history
    ON signal_history (asset, created_at DESC)
    WHERE archived = FALSE;
