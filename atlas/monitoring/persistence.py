"""PostgreSQL persistence for hourly monitoring snapshots."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import asyncpg
import msgspec
from loguru import logger

from atlas.jobs.outcome_horizon_job import normalize_asset_base, validate_price_snapshots_table_ident
from atlas.monitoring.models import AssetHourlyAnalytics, HourlyMonitorCycleResult, NormalizedHourlyQuote


async def persist_hourly_quotes(
    conn: asyncpg.Connection,
    *,
    quotes: list[NormalizedHourlyQuote],
    price_table: str,
) -> int:
    """Insert normalized quotes and mirror closes into ``price_snapshots``."""
    table_ident = validate_price_snapshots_table_ident(price_table)
    written = 0
    for quote in quotes:
        if quote.price_usd <= 0:
            continue
        base = normalize_asset_base(quote.asset_base)
        await conn.execute(
            f"""
            INSERT INTO hourly_market_snapshots (
                sampled_at, asset, price_usd, volume_24h_usd, market_cap_usd,
                source_provider, source_key_id, quality, payload_json
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            ON CONFLICT (asset, sampled_at) DO UPDATE SET
                price_usd = EXCLUDED.price_usd,
                volume_24h_usd = EXCLUDED.volume_24h_usd,
                market_cap_usd = EXCLUDED.market_cap_usd,
                source_provider = EXCLUDED.source_provider,
                source_key_id = EXCLUDED.source_key_id,
                quality = EXCLUDED.quality,
                payload_json = EXCLUDED.payload_json
            """,
            quote.sampled_at,
            base,
            quote.price_usd,
            quote.volume_24h_usd,
            quote.market_cap_usd,
            quote.source_provider,
            quote.source_key_id,
            quote.quality,
            msgspec.json.encode(quote.model_dump(mode="json")).decode(),
            timeout=30.0,
        )
        await conn.execute(
            f"""
            INSERT INTO {table_ident} (sampled_at, asset, close_px)
            VALUES ($1, $2, $3)
            ON CONFLICT (asset, sampled_at) DO UPDATE SET close_px = EXCLUDED.close_px
            """,
            quote.sampled_at,
            base,
            quote.price_usd,
            timeout=30.0,
        )
        written += 1
    return written


async def persist_hourly_analytics(
    conn: asyncpg.Connection,
    *,
    per_asset: list[AssetHourlyAnalytics],
    cycle: HourlyMonitorCycleResult,
) -> int:
    """Store analytics row per asset plus one basket summary row."""
    written = 0
    for row in per_asset:
        await conn.execute(
            """
            INSERT INTO hourly_market_analytics (
                sampled_at, asset, hourly_return_pct, rolling_volatility_pct,
                momentum_pct, volume_z_score, market_cap_rank, rank_change,
                relative_strength, basket_breadth_pct, payload_json
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            ON CONFLICT (asset, sampled_at) DO UPDATE SET
                hourly_return_pct = EXCLUDED.hourly_return_pct,
                rolling_volatility_pct = EXCLUDED.rolling_volatility_pct,
                momentum_pct = EXCLUDED.momentum_pct,
                volume_z_score = EXCLUDED.volume_z_score,
                market_cap_rank = EXCLUDED.market_cap_rank,
                rank_change = EXCLUDED.rank_change,
                relative_strength = EXCLUDED.relative_strength,
                basket_breadth_pct = EXCLUDED.basket_breadth_pct,
                payload_json = EXCLUDED.payload_json
            """,
            row.sampled_at,
            normalize_asset_base(row.asset_base),
            row.hourly_return_pct,
            row.rolling_volatility_pct,
            row.momentum_pct,
            row.volume_z_score,
            row.market_cap_rank,
            row.rank_change,
            row.relative_strength,
            cycle.basket.breadth_positive_pct,
            msgspec.json.encode(row.model_dump(mode="json")).decode(),
            timeout=30.0,
        )
        written += 1
    return written


async def load_price_history(
    conn: asyncpg.Connection,
    *,
    asset_base: str,
    before: datetime,
    limit: int,
    price_table: str,
) -> list[Decimal]:
    """Recent hourly closes oldest-first for momentum / return windows."""
    table_ident = validate_price_snapshots_table_ident(price_table)
    base = normalize_asset_base(asset_base)
    rows = await conn.fetch(
        f"""
        SELECT close_px FROM {table_ident}
        WHERE asset = $1 AND sampled_at <= $2
        ORDER BY sampled_at DESC
        LIMIT $3
        """,
        base,
        before,
        limit,
        timeout=15.0,
    )
    prices: list[Decimal] = []
    for row in reversed(rows):
        try:
            prices.append(Decimal(str(row["close_px"])))
        except Exception:
            continue
    return prices


async def load_return_history(
    conn: asyncpg.Connection,
    *,
    asset_base: str,
    before: datetime,
    limit: int,
) -> list[float]:
    """Prior hourly returns for volatility."""
    base = normalize_asset_base(asset_base)
    rows = await conn.fetch(
        """
        SELECT hourly_return_pct FROM hourly_market_analytics
        WHERE asset = $1 AND sampled_at < $2 AND hourly_return_pct IS NOT NULL
        ORDER BY sampled_at DESC
        LIMIT $3
        """,
        base,
        before,
        limit,
        timeout=15.0,
    )
    values: list[float] = []
    for row in reversed(rows):
        raw = row["hourly_return_pct"]
        if raw is not None:
            values.append(float(raw))
    return values


async def load_volume_history(
    conn: asyncpg.Connection,
    *,
    asset_base: str,
    before: datetime,
    limit: int,
) -> list[Decimal]:
    """Prior 24h volumes for z-score."""
    base = normalize_asset_base(asset_base)
    rows = await conn.fetch(
        """
        SELECT volume_24h_usd FROM hourly_market_snapshots
        WHERE asset = $1 AND sampled_at < $2 AND volume_24h_usd IS NOT NULL
        ORDER BY sampled_at DESC
        LIMIT $3
        """,
        base,
        before,
        limit,
        timeout=15.0,
    )
    volumes: list[Decimal] = []
    for row in reversed(rows):
        try:
            volumes.append(Decimal(str(row["volume_24h_usd"])))
        except Exception:
            logger.debug("volume_history_row_skipped | asset={}", asset_base)
    return volumes
