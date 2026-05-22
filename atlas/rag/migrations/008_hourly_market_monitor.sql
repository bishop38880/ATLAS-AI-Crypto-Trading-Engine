-- 008_hourly_market_monitor.sql — hourly ingest + analytics for daily-8 basket

CREATE TABLE IF NOT EXISTS hourly_market_snapshots (
    sampled_at       TIMESTAMPTZ NOT NULL,
    asset            TEXT        NOT NULL,
    price_usd        NUMERIC(24, 12) NOT NULL,
    volume_24h_usd   NUMERIC(24, 4),
    market_cap_usd   NUMERIC(24, 4),
    source_provider  TEXT        NOT NULL,
    source_key_id    TEXT        NOT NULL,
    quality          TEXT        NOT NULL DEFAULT 'full',
    payload_json     TEXT,
    PRIMARY KEY (asset, sampled_at)
);

CREATE INDEX IF NOT EXISTS idx_hourly_market_snapshots_time
    ON hourly_market_snapshots (sampled_at DESC);

CREATE TABLE IF NOT EXISTS hourly_market_analytics (
    sampled_at              TIMESTAMPTZ NOT NULL,
    asset                   TEXT        NOT NULL,
    hourly_return_pct       DOUBLE PRECISION,
    rolling_volatility_pct  DOUBLE PRECISION,
    momentum_pct            DOUBLE PRECISION,
    volume_z_score          DOUBLE PRECISION,
    market_cap_rank         INTEGER,
    rank_change             INTEGER,
    relative_strength       DOUBLE PRECISION,
    basket_breadth_pct      DOUBLE PRECISION,
    payload_json            TEXT,
    PRIMARY KEY (asset, sampled_at)
);

CREATE INDEX IF NOT EXISTS idx_hourly_market_analytics_time
    ON hourly_market_analytics (sampled_at DESC);
