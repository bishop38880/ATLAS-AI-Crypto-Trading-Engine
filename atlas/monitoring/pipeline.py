"""Orchestrate one hourly monitoring cycle."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import asyncpg
import httpx
import msgspec
import redis.asyncio as redis_async
from loguru import logger

from atlas.core.monitoring_telemetry import record_monitoring_alert
from atlas.monitoring.active_assets import resolve_daily_8_asset_bases
from atlas.monitoring.alerts import evaluate_hourly_alerts
from atlas.monitoring.analytics import (
    build_asset_analytics,
    build_basket_analytics,
    calculate_hourly_return_pct,
    calculate_rank_changes,
)
from atlas.monitoring.models import HourlyMonitorCycleResult
from atlas.monitoring.persistence import (
    load_price_history,
    load_return_history,
    load_volume_history,
    persist_hourly_analytics,
    persist_hourly_quotes,
)
from atlas.monitoring.router import HourlyProviderRouter
from atlas.monitoring.sources.coingecko import CoinGeckoHourlySource, build_coingecko_key_pool
from atlas.monitoring.sources.pyth import PythCachedHourlySource
from atlas.shared.config import PolarisSettings

_LATEST_KEY = "polaris:monitoring:hourly:latest"


def _market_cap_rank_map(quotes: list) -> dict[str, int]:
    """Rank by market cap descending; missing caps sort last."""
    sortable = [
        (quote.asset_base, quote.market_cap_usd or Decimal("0"))
        for quote in quotes
    ]
    sortable.sort(key=lambda row: row[1], reverse=True)
    return {asset: index + 1 for index, (asset, _) in enumerate(sortable)}


async def run_hourly_monitor_cycle(
    *,
    settings: PolarisSettings,
    redis_client: redis_async.Redis,  # type: ignore[type-arg]
    pg_pool: asyncpg.Pool | None,
    http_client: httpx.AsyncClient | None = None,
) -> HourlyMonitorCycleResult:
    """Ingest, analyze, persist, and publish one hourly bucket."""
    sampled_at = datetime.now(timezone.utc)
    assets = await resolve_daily_8_asset_bases(redis_client)
    client = http_client or httpx.AsyncClient(
        timeout=httpx.Timeout(settings.hourly_monitor_http_timeout_s),
        http2=True,
    )
    owns_client = http_client is None

    coingecko = CoinGeckoHourlySource(
        settings,
        client,
        build_coingecko_key_pool(settings),
        redis_client,
    )
    router = HourlyProviderRouter(settings, coingecko, PythCachedHourlySource(redis_client), redis_client)

    quotes = []
    for asset in assets:
        try:
            quotes.append(await router.fetch_normalized_quote(asset, sampled_at))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("monitoring_asset_cycle_failed | asset={} | err={}", asset, exc)

    ranks_now = _market_cap_rank_map(quotes)
    ranks_prev: dict[str, int] = {}
    if pg_pool is not None:
        async with pg_pool.acquire() as conn:
            for asset in assets:
                row = await conn.fetchrow(
                    """
                    SELECT market_cap_rank FROM hourly_market_analytics
                    WHERE asset = $1 AND market_cap_rank IS NOT NULL
                    ORDER BY sampled_at DESC LIMIT 1
                    """,
                    asset,
                    timeout=10.0,
                )
                if row is not None:
                    ranks_prev[asset] = int(row["market_cap_rank"])
    rank_changes = calculate_rank_changes(ranks_now, ranks_prev)

    per_asset_analytics = []
    returns_for_basket: list[float | None] = []
    for quote in quotes:
        previous_price: Decimal | None = None
        return_history: list[float] = []
        price_history: list[Decimal] = []
        volume_history: list[Decimal] = []
        if pg_pool is not None:
            async with pg_pool.acquire() as conn:
                prices = await load_price_history(
                    conn,
                    asset_base=quote.asset_base,
                    before=sampled_at,
                    limit=settings.hourly_monitor_volatility_window + 1,
                    price_table=settings.price_snapshots_table,
                )
                price_history = prices
                if prices:
                    previous_price = prices[-1]
                return_history = await load_return_history(
                    conn,
                    asset_base=quote.asset_base,
                    before=sampled_at,
                    limit=settings.hourly_monitor_volatility_window,
                )
                volume_history = await load_volume_history(
                    conn,
                    asset_base=quote.asset_base,
                    before=sampled_at,
                    limit=settings.hourly_monitor_volatility_window,
                )

        hourly_return = calculate_hourly_return_pct(quote.price_usd, previous_price)
        returns_for_basket.append(hourly_return)
        per_asset_analytics.append(
            build_asset_analytics(
                quote=quote,
                previous_price=previous_price,
                return_history=return_history,
                price_history=price_history,
                volume_history=volume_history,
                market_cap_rank=ranks_now.get(quote.asset_base),
                rank_change=rank_changes.get(quote.asset_base),
                basket_return_pct=None,
            )
        )

    valid_returns = [value for value in returns_for_basket if value is not None]
    basket_return = sum(valid_returns) / len(valid_returns) if valid_returns else None
    if basket_return is not None:
        per_asset_analytics = [
            row.model_copy(
                update={
                    "relative_strength": (
                        (row.hourly_return_pct - basket_return)
                        if row.hourly_return_pct is not None
                        else None
                    )
                }
            )
            for row in per_asset_analytics
        ]

    basket = build_basket_analytics(sampled_at=sampled_at, asset_returns=returns_for_basket)
    alert_tuples = evaluate_hourly_alerts(settings, quotes, per_asset_analytics)
    for level, source, message in alert_tuples:
        await record_monitoring_alert(
            redis_client,
            level=level,
            message=message,
            source=source,
            alert_id=f"hourly:{message[:48]}",
        )

    result = HourlyMonitorCycleResult(
        sampled_at=sampled_at,
        assets=tuple(assets),
        quotes=tuple(quotes),
        per_asset_analytics=tuple(per_asset_analytics),
        basket=basket,
        fetch_meta=router.fetch_meta,
        alert_count=len(alert_tuples),
    )

    if pg_pool is not None:
        async with pg_pool.acquire() as conn:
            await persist_hourly_quotes(
                conn,
                quotes=list(quotes),
                price_table=settings.price_snapshots_table,
            )
            await persist_hourly_analytics(conn, per_asset=list(per_asset_analytics), cycle=result)

    await redis_client.setex(
        _LATEST_KEY,
        settings.hourly_monitor_redis_ttl_seconds,
        msgspec.json.encode(result.model_dump(mode="json")),
    )

    if owns_client:
        await client.aclose()

    logger.info(
        "hourly_monitor_cycle_complete | assets={} | alerts={}",
        len(assets),
        len(alert_tuples),
    )
    return result
