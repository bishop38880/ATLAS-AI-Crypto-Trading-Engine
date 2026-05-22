-- L3 Order Book Snapshots — TimescaleDB hypertable
-- Phase 9: Slippage-Adjusted Backtesting
--
-- Stores 100ms-granularity L3 order book snapshots from
-- Kraken / Bitget WebSocket feed for agent-based replay.

CREATE TABLE IF NOT EXISTS l3_order_book (
    time         TIMESTAMPTZ    NOT NULL,
    symbol       TEXT           NOT NULL,
    side         TEXT           NOT NULL CHECK (side IN ('bid', 'ask')),
    price        NUMERIC(20,8) NOT NULL,
    size         NUMERIC(20,8) NOT NULL,
    order_id     TEXT,
    sequence_num BIGINT         NOT NULL
);

-- Convert to TimescaleDB hypertable (100ms chunks = ~864k rows/day per symbol).
SELECT create_hypertable('l3_order_book', 'time', if_not_exists => TRUE);

-- Indexes for replay queries.
CREATE INDEX IF NOT EXISTS idx_l3_ob_symbol_time
    ON l3_order_book (symbol, time DESC);

CREATE INDEX IF NOT EXISTS idx_l3_ob_symbol_side_time
    ON l3_order_book (symbol, side, time DESC);
