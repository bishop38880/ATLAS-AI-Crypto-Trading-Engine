-- 006_price_snapshots.sql — OHLC-free reference closes for horizon outcomes
--
-- Used by atlas.jobs.outcome_horizon_job to back-fill signal_history.metadata
-- outcome_pct_1h / _4h / _24h. Populate via your ingest pipeline (Bitget/Pyth/etc.).
--
-- Plain PostgreSQL table (Timescale hypertable optional — same column names).

CREATE TABLE IF NOT EXISTS price_snapshots (
    sampled_at TIMESTAMPTZ NOT NULL,
    asset      TEXT        NOT NULL,
    close_px   NUMERIC(24, 12) NOT NULL,
    PRIMARY KEY (asset, sampled_at)
);

CREATE INDEX IF NOT EXISTS idx_price_snapshots_time
    ON price_snapshots (sampled_at DESC);
