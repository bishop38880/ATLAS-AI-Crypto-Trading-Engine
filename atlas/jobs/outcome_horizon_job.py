"""Fill ``signal_history.metadata`` horizon outcome % from ``price_snapshots``.

Runs as a cancellable asyncio task from ``backend.main`` lifespan when
``ATLAS_OUTCOME_HORIZON_JOB_ENABLED=true``.
"""

from __future__ import annotations

import asyncio
import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import asyncpg
from loguru import logger

from atlas.shared.config import PolarisSettings

_LONG_DECISIONS = frozenset({"Strong Buy", "Buy"})
_SHORT_DECISIONS = frozenset({"Strong Sell", "Sell"})
_SKIP_DECISIONS = frozenset({"Hold", "No Position"})


def validate_price_snapshots_table_ident(name: str) -> str:
    cleaned = name.strip()
    if not cleaned or not cleaned.replace("_", "").isalnum():
        raise ValueError("invalid_price_snapshots_table_ident")
    return cleaned


def horizon_return_pct(decision: str, entry_px: Decimal, horizon_px: Decimal) -> float | None:
    """Signed % move supporting LONG decisions (buy side) and SHORT decisions."""
    if entry_px <= 0:
        return None
    if decision in _LONG_DECISIONS:
        return float((horizon_px - entry_px) / entry_px * Decimal("100"))
    if decision in _SHORT_DECISIONS:
        return float((entry_px - horizon_px) / entry_px * Decimal("100"))
    return None


def normalize_asset_base(asset: str) -> str:
    s = asset.upper().strip().replace("-", "").replace("_", "").replace("/", "")
    if s.endswith("USDT") and len(s) > 4:
        return s[:-4]
    return s


async def fetch_reference_price(
    conn: asyncpg.Connection,
    table_sql_ident: str,
    asset_base: str,
    at_or_before: datetime,
) -> Decimal | None:
    """Nearest snapshot at or before ``at_or_before``."""
    query = (
        "SELECT close_px FROM {} WHERE asset = $1 AND sampled_at <= $2 "
        "ORDER BY sampled_at DESC LIMIT 1"
    ).format(table_sql_ident)
    row = await conn.fetchrow(query, asset_base, at_or_before, timeout=15.0)
    if row is None:
        return None
    raw = row["close_px"]
    try:
        return Decimal(str(raw))
    except Exception:
        return None


async def run_horizon_tick(pool: asyncpg.Pool, table: str) -> int:
    """Process up to 120 eligible rows; returns count of rows patched."""
    now = datetime.now(timezone.utc)
    updated_rows = 0
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT signal_id, asset, decision, created_at, metadata
            FROM signal_history
            WHERE decision NOT IN ('Hold', 'No Position')
              AND created_at < NOW() - INTERVAL '1 hour'
            ORDER BY created_at DESC
            LIMIT 120
            """,
            timeout=30.0,
        )
        for row in rows:
            decision = str(row["decision"])
            if decision in _SKIP_DECISIONS:
                continue

            meta_raw = row["metadata"]
            meta: dict[str, Any] = dict(meta_raw) if isinstance(meta_raw, dict) else {}

            created_at = row["created_at"]
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)

            asset_base = normalize_asset_base(str(row["asset"]))
            patches: dict[str, Any] = {}

            entry_px = await fetch_reference_price(conn, table, asset_base, created_at)
            if entry_px is None:
                continue

            if (
                meta.get("outcome_pct_1h") is None
                and now >= created_at + timedelta(hours=1)
            ):
                t1 = created_at + timedelta(hours=1)
                px1 = await fetch_reference_price(conn, table, asset_base, t1)
                if px1 is not None:
                    pct = horizon_return_pct(decision, entry_px, px1)
                    if pct is not None:
                        patches["outcome_pct_1h"] = round(pct, 6)

            if (
                meta.get("outcome_pct_4h") is None
                and now >= created_at + timedelta(hours=4)
            ):
                t4 = created_at + timedelta(hours=4)
                px4 = await fetch_reference_price(conn, table, asset_base, t4)
                if px4 is not None:
                    pct = horizon_return_pct(decision, entry_px, px4)
                    if pct is not None:
                        patches["outcome_pct_4h"] = round(pct, 6)

            if (
                meta.get("outcome_pct_24h") is None
                and now >= created_at + timedelta(hours=24)
            ):
                t24 = created_at + timedelta(hours=24)
                px24 = await fetch_reference_price(conn, table, asset_base, t24)
                if px24 is not None:
                    pct = horizon_return_pct(decision, entry_px, px24)
                    if pct is not None:
                        patches["outcome_pct_24h"] = round(pct, 6)

            if not patches:
                continue

            await conn.execute(
                """
                UPDATE signal_history
                SET metadata = COALESCE(metadata, '{}'::jsonb) || $1::jsonb
                WHERE signal_id = $2
                """,
                patches,
                str(row["signal_id"]),
                timeout=15.0,
            )
            updated_rows += 1

    if updated_rows:
        logger.info(
            "outcome_horizon_tick_complete | rows_updated={} | table={}",
            updated_rows,
            table,
        )
    return updated_rows


async def outcome_horizon_loop(pool: asyncpg.Pool, settings: PolarisSettings) -> None:
    """Infinite jittered loop until cancelled."""
    table = validate_price_snapshots_table_ident(settings.price_snapshots_table)
    interval = int(settings.outcome_horizon_job_interval_seconds)
    jitter = max(5, min(120, interval // 8))
    logger.info(
        "outcome_horizon_loop_started | interval_s={} | table={}",
        interval,
        table,
    )
    while True:
        try:
            await run_horizon_tick(pool, table)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("outcome_horizon_tick_failed | err={}", str(exc))
        sleep_s = interval + random.uniform(-jitter, jitter)
        await asyncio.sleep(max(30.0, sleep_s))
