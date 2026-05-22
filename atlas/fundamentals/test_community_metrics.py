"""Unit tests for community fundamentals composites."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

from atlas.fundamentals.community_metrics import (
    compute_community_growth_7d_pct,
    compute_community_size_score,
)
from atlas.providers.coingecko.models import CommunityData


@pytest.mark.parametrize(
    ("twitter_followers", "reddit_subscribers", "telegram_users"),
    [
        (1_000_000, 750_000, 120_500),
        (900, 880, 870),
        (250_003, None, None),
    ],
)
def test_compute_community_size_score_all_fields_present(
    twitter_followers: int,
    reddit_subscribers: int | None,
    telegram_users: int | None,
) -> None:
    """Synthetic audiences always land inside `[0.0, 1.0]`."""
    sample = CommunityData(
        asset_symbol="ETH",
        twitter_followers=twitter_followers,
        reddit_subscribers=reddit_subscribers,
        reddit_average_posts_48h=4.25,
        reddit_average_comments_48h=None,
        reddit_accounts_active_48h=None,
        telegram_channel_user_count=telegram_users,
        last_updated_utc=datetime.now(timezone.utc),
        status="healthy",
    )

    magnitude = compute_community_size_score(sample)

    assert 0.0 <= magnitude <= 1.0


def test_compute_community_size_score_all_none_returns_zero() -> None:
    """Missing counts degrade to logarithms of unity → zero numerator."""
    model = CommunityData(
        asset_symbol="ZZZ",
        twitter_followers=None,
        reddit_subscribers=None,
        reddit_average_posts_48h=None,
        reddit_average_comments_48h=None,
        reddit_accounts_active_48h=None,
        telegram_channel_user_count=None,
        last_updated_utc=datetime.now(timezone.utc),
        status="healthy",
    )

    assert compute_community_size_score(model) == pytest.approx(0.0, abs=1e-12)


def test_compute_community_size_score_clamped_at_one() -> None:
    """Astronomical totals never exceed dimensional ceiling."""
    huge = CommunityData(
        asset_symbol="MEGA",
        twitter_followers=2_500_000_000,
        reddit_subscribers=2_000_000_000,
        telegram_channel_user_count=1_500_000_000,
        reddit_average_posts_48h=None,
        reddit_average_comments_48h=None,
        reddit_accounts_active_48h=None,
        last_updated_utc=datetime.now(timezone.utc),
        status="healthy",
    )

    assert compute_community_size_score(huge) <= 1.0 + 1e-9


@pytest.mark.asyncio
async def test_compute_community_growth_7d_returns_none_without_history() -> None:
    """Empty history ⇒ no growth estimate."""

    class EmptyConn:
        async def fetchrow(self, *_args: object, **_kw: Any) -> None:
            return None

    result = await compute_community_growth_7d_pct("BTC", EmptyConn())  # type: ignore[arg-type]

    assert result is None


@pytest.mark.asyncio
async def test_compute_community_growth_7d_returns_none_with_single_snapshot() -> None:
    """One observation cannot bootstrap a denominator pair."""

    class SingleConn:
        def __init__(self) -> None:
            self._step = 0

        async def fetchrow(self, *_args: object, **_kw: Any) -> dict[str, object] | None:
            now = datetime(2026, 3, 1, tzinfo=timezone.utc)
            if self._step == 0:
                self._step += 1
                return {
                    "id": 9,
                    "fetched_at": now,
                    "twitter_followers": 100,
                    "reddit_subscribers": 0,
                    "reddit_avg_posts_48h": None,
                    "reddit_avg_comments_48h": None,
                    "reddit_active_48h": None,
                    "telegram_users": None,
                }
            return None

    assert await compute_community_growth_7d_pct("BTC", SingleConn()) is None  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_compute_community_growth_7d_computes_delta() -> None:
    """Two spaced rows yield deterministic Decimal growth fractions."""
    now = datetime(2026, 4, 5, tzinfo=timezone.utc)
    past = now - timedelta(days=14)

    newer_row = {
        "id": 1002,
        "fetched_at": now,
        "twitter_followers": 99999,
        "reddit_subscribers": 0,
        "reddit_avg_posts_48h": None,
        "reddit_avg_comments_48h": None,
        "reddit_active_48h": None,
        "telegram_users": None,
    }

    older_row = {
        "fetched_at": past,
        "twitter_followers": 9999,
        "reddit_subscribers": 0,
        "reddit_avg_posts_48h": None,
        "reddit_avg_comments_48h": None,
        "reddit_active_48h": None,
        "telegram_users": None,
    }

    class PairConn:
        def __init__(self) -> None:
            self._step = 0

        async def fetchrow(self, *_args: object, **_kw: Any) -> dict[str, object] | None:
            if self._step == 0:
                self._step += 1
                return newer_row
            self._step += 1
            return older_row

    recent_model = CommunityData(
        asset_symbol="BTC",
        twitter_followers=99999,
        reddit_subscribers=0,
        reddit_average_posts_48h=None,
        reddit_average_comments_48h=None,
        reddit_accounts_active_48h=None,
        telegram_channel_user_count=None,
        last_updated_utc=now,
        status="healthy",
    )

    prior_model = CommunityData(
        asset_symbol="BTC",
        twitter_followers=9999,
        reddit_subscribers=0,
        reddit_average_posts_48h=None,
        reddit_average_comments_48h=None,
        reddit_accounts_active_48h=None,
        telegram_channel_user_count=None,
        last_updated_utc=past,
        status="healthy",
    )

    expected_recent = Decimal(str(compute_community_size_score(recent_model)))
    expected_prior_mag = Decimal(str(compute_community_size_score(prior_model)))
    expected_decimal = (expected_recent - expected_prior_mag) / expected_prior_mag

    actual = await compute_community_growth_7d_pct("BTC", PairConn())  # type: ignore[arg-type]

    assert actual is not None
    assert actual.quantize(Decimal("0.000001")) == expected_decimal.quantize(Decimal("0.000001"))
