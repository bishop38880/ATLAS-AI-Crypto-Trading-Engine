-- Fundamental community engagement snapshots sourced from CoinGecko (cold path only).
CREATE TABLE IF NOT EXISTS asset_community_history (
    id BIGSERIAL PRIMARY KEY,
    asset TEXT NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    twitter_followers INTEGER,
    reddit_subscribers INTEGER,
    reddit_avg_posts_48h DOUBLE PRECISION,
    reddit_avg_comments_48h DOUBLE PRECISION,
    reddit_active_48h DOUBLE PRECISION,
    telegram_users INTEGER,
    source TEXT NOT NULL DEFAULT 'coingecko'
);

CREATE INDEX IF NOT EXISTS idx_community_history_asset_time 
    ON asset_community_history (asset, fetched_at DESC);
