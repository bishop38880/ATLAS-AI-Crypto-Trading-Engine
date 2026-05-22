-- Decision provenance ledger — append-only, no UPDATE/DELETE.
--
-- One row per emitted signal.  Provides full audit trail for
-- post-trade reproducibility ("given inputs at time T, was the
-- signal correct?").
--
-- Table is INSERT-only by design.  Reads are for post-mortems
-- and the Dashboard DecisionLog panel.

CREATE TABLE IF NOT EXISTS decision_provenance (
    id              BIGSERIAL PRIMARY KEY,
    signal_id       TEXT NOT NULL,
    timestamp       TIMESTAMPTZ NOT NULL,
    schema_version  TEXT NOT NULL,
    git_sha         TEXT NOT NULL DEFAULT 'unknown',
    input_bundle_sha256   TEXT NOT NULL,
    agent_verdicts        JSONB NOT NULL,
    raw_confluence_score  SMALLINT NOT NULL CHECK (raw_confluence_score BETWEEN 0 AND 220),
    normalised_score      SMALLINT NOT NULL CHECK (normalised_score BETWEEN 0 AND 100),
    decision              TEXT NOT NULL,
    pipeline_confidence   REAL NOT NULL DEFAULT 0.0,
    confidence_tier       TEXT NOT NULL DEFAULT 'STANDARD',
    signal_output_sha256  TEXT NOT NULL,
    cycle_latency_ms      REAL NOT NULL DEFAULT 0.0,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Fast lookup by signal_id (correlation key from SignalOutput)
CREATE INDEX IF NOT EXISTS idx_provenance_signal_id
    ON decision_provenance (signal_id);

-- Time-range queries for post-mortems
CREATE INDEX IF NOT EXISTS idx_provenance_timestamp
    ON decision_provenance (timestamp);

-- Prevent accidental duplicate inserts for the same signal
CREATE UNIQUE INDEX IF NOT EXISTS idx_provenance_signal_unique
    ON decision_provenance (signal_id);

COMMENT ON TABLE decision_provenance IS
    'Append-only decision audit trail. One row per emitted signal. '
    'Never UPDATE or DELETE — this is the provenance ledger.';

CREATE TABLE IF NOT EXISTS shadow_gnn_scores_advanced (
    id BIGSERIAL PRIMARY KEY,
    asset TEXT NOT NULL,
    contagion_signal NUMERIC(6,4) NOT NULL,
    wavelet_features JSONB NOT NULL,
    stress_triggered BOOLEAN NOT NULL DEFAULT FALSE,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_shadow_advanced_asset_time
    ON shadow_gnn_scores_advanced (asset, computed_at DESC);
