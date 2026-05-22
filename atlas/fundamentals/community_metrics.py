"""Derived fundamentals metrics built from CoinGecko community snapshots."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import asyncpg

from atlas.providers.coingecko.models import CommunityData

_LOG10_CAP = math.log10(10_000_000.0) * 3.0


def compute_community_size_score(community: CommunityData) -> float:
    """
    Log-normalized composite of twitter + reddit + telegram audience size.

    Normalization divides by ``log10(1e7) * three channels`` because major assets
    often land near tens of millions of followers across stacks; logarithms
    collapse the ~4-order-of-magnitude spread into an interpretable magnitude.
    Missing counts are treated as zero. Returned score is bounded to ``[0, 1]``
    via clamping — used strictly for fundamentals analytics and admission gates.
    """
    followers_twitter = community.twitter_followers or 0
    subscribers_reddit = community.reddit_subscribers or 0
    telegram_users = community.telegram_channel_user_count or 0
    numerator = (
        math.log10(float(followers_twitter + 1))
        + math.log10(float(subscribers_reddit + 1))
        + math.log10(float(telegram_users + 1))
    )
    magnitude = numerator / _LOG10_CAP if _LOG10_CAP else 0.0
    return max(0.0, min(1.0, magnitude))


def _community_from_snapshot_row(asset: str, row: asyncpg.Record) -> CommunityData:
    """Project a Postgres row back into CommunityData without extra I/O."""
    fetched_ts: datetime = row["fetched_at"]
    if fetched_ts.tzinfo is None:
        fetched_ts = fetched_ts.replace(tzinfo=timezone.utc)
    return CommunityData(
        asset_symbol=asset,
        twitter_followers=row["twitter_followers"],
        reddit_subscribers=row["reddit_subscribers"],
        reddit_average_posts_48h=row["reddit_avg_posts_48h"],
        reddit_average_comments_48h=row["reddit_avg_comments_48h"],
        reddit_accounts_active_48h=row["reddit_active_48h"],
        telegram_channel_user_count=row["telegram_users"],
        last_updated_utc=fetched_ts,
        status="healthy",
    )


async def _fetch_newest_snapshot(
    conn: asyncpg.Connection,
    asset: str,
) -> asyncpg.Record | None:
    """Pull the freshest fundamentals row."""
    sql = """
        SELECT id, fetched_at, twitter_followers, reddit_subscribers,
               reddit_avg_posts_48h, reddit_avg_comments_48h,
               reddit_active_48h, telegram_users
        FROM asset_community_history
        WHERE asset = $1
        ORDER BY fetched_at DESC
        LIMIT 1
    """
    return await conn.fetchrow(sql, asset, timeout=15.0)


async def _fetch_prior_snapshot_for_growth(
    conn: asyncpg.Connection,
    asset: str,
    anchor_dt: datetime,
    newest_pk: int,
) -> asyncpg.Record | None:
    """Locate the freshest row at least seven days older than newest."""
    sql = """
        SELECT fetched_at, twitter_followers, reddit_subscribers,
               reddit_avg_posts_48h, reddit_avg_comments_48h,
               reddit_active_48h, telegram_users
        FROM asset_community_history
        WHERE asset = $1
          AND fetched_at <= $2
          AND id <> $3
        ORDER BY fetched_at DESC
        LIMIT 1
    """
    return await conn.fetchrow(sql, asset, anchor_dt, newest_pk, timeout=15.0)


async def compute_community_growth_7d_pct(
    asset: str,
    conn: asyncpg.Connection,
) -> Decimal | None:
    """
    Week-over-week percentage change measured on rolling ``community_size_score``.

    The newest observation pairs with the most recent row at least seven days
    older than that timestamp. Requires two distinct rows spanning the window.

    Returns:
        Fractional Decimal or ``None`` if history is insufficient or denominator zero.
    """
    newest_row = await _fetch_newest_snapshot(conn, asset)
    if newest_row is None:
        return None

    anchor_dt: datetime = newest_row["fetched_at"] - timedelta(days=7)
    if anchor_dt.tzinfo is None:
        anchor_dt = anchor_dt.replace(tzinfo=timezone.utc)

    prior_row = await _fetch_prior_snapshot_for_growth(conn, asset, anchor_dt, newest_row["id"])
    if prior_row is None:
        return None

    recent_model = _community_from_snapshot_row(asset, newest_row)
    prior_model = _community_from_snapshot_row(asset, prior_row)

    magnitude_before = Decimal(str(compute_community_size_score(prior_model)))
    magnitude_after = Decimal(str(compute_community_size_score(recent_model)))
    if magnitude_before == 0:
        return None
    return (magnitude_after - magnitude_before) / magnitude_before
