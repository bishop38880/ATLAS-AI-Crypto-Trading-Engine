-- 004_state_machine.sql — State Machine Schema
-- Session: Memory Lifecycle & Garbage Collection

-- 1. Add state machine columns to signal_history
-- (escore is already NUMERIC(6,4) from 003_agent_zero.sql, but we ensure it here)
ALTER TABLE signal_history
    ADD COLUMN IF NOT EXISTS document_state TEXT NOT NULL DEFAULT 'raw',
    ADD COLUMN IF NOT EXISTS contradiction_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS state_changed_at TIMESTAMPTZ;

-- 2. Create the state_transition_log table
CREATE TABLE IF NOT EXISTS state_transition_log (
    id                            BIGSERIAL PRIMARY KEY,
    document_id                   TEXT NOT NULL,
    asset                         TEXT NOT NULL,
    from_state                    TEXT NOT NULL,
    to_state                      TEXT NOT NULL,
    reason                        TEXT NOT NULL,
    triggered_by                  TEXT NOT NULL,
    escore_at_change              NUMERIC(5,4),
    contradiction_count_at_change INTEGER,
    transitioned_at               TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_state_transition_doc
    ON state_transition_log (document_id, transitioned_at DESC);

CREATE INDEX IF NOT EXISTS idx_state_transition_asset
    ON state_transition_log (asset, to_state, transitioned_at DESC);

-- 3. Create the contradiction_accumulation table
CREATE TABLE IF NOT EXISTS contradiction_accumulation (
    id                   BIGSERIAL PRIMARY KEY,
    target_document_id   TEXT NOT NULL,
    source_document_id   TEXT NOT NULL,
    asset                TEXT NOT NULL,
    target_decision      TEXT NOT NULL,
    source_decision      TEXT NOT NULL,
    target_score         INTEGER NOT NULL,
    source_score         INTEGER NOT NULL,
    time_delta_days      NUMERIC(6,2) NOT NULL,
    recorded_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_contradiction_target
    ON contradiction_accumulation (target_document_id, recorded_at DESC);

-- 4. Fast state lookup
CREATE INDEX IF NOT EXISTS idx_signal_history_state
    ON signal_history (document_state, asset, created_at DESC)
    WHERE document_state NOT IN ('archived');
