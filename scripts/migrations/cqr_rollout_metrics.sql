-- Phase 10: CQR Graduated Rollout — Metrics Table
-- Tracks per-stage performance for SHADOW → ACTIVE_TIGHT_ONLY → FULL promotion.

CREATE TABLE IF NOT EXISTS cqr_rollout_metrics (
    id                      BIGSERIAL PRIMARY KEY,
    timestamp               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    stage                   TEXT NOT NULL CHECK (
                                stage IN (
                                    'SHADOW', 'ACTIVE_TIGHT_ONLY', 'FULL',
                                    'PROMOTION_SUGGESTED:ACTIVE_TIGHT_ONLY',
                                    'PROMOTION_SUGGESTED:FULL'
                                )
                            ),
    avg_width               DOUBLE PRECISION NOT NULL,
    num_trades_using_bounds  INTEGER NOT NULL DEFAULT 0,
    sharpe_bounds            DOUBLE PRECISION,
    sharpe_point             DOUBLE PRECISION
);

CREATE INDEX IF NOT EXISTS idx_cqr_rollout_ts
    ON cqr_rollout_metrics (timestamp DESC);

COMMENT ON TABLE cqr_rollout_metrics IS
    'Phase 10 — graduated CQR rollout performance tracking';
