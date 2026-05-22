"""Admission filter tests for fundamentals community thresholds."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from atlas.universe.admission_filter import evaluate_community_admission
from atlas.providers.coingecko.models import CommunityData


def _community(
    *,
    reddit: int | None,
    telegram: int | None,
    status: str = "healthy",
) -> CommunityData:
    return CommunityData(
        asset_symbol="GHOST",
        twitter_followers=None,
        reddit_subscribers=reddit,
        reddit_average_posts_48h=None,
        reddit_average_comments_48h=None,
        reddit_accounts_active_48h=None,
        telegram_channel_user_count=telegram,
        last_updated_utc=datetime.now(timezone.utc),
        status=status,  # type: ignore[arg-type]
    )


def test_admission_rejects_ghost_project() -> None:
    """Ultra-thin social graphs fail the Reddit/Telegram floor."""
    assert evaluate_community_admission(_community(reddit=10, telegram=5)) is False


def test_admission_accepts_real_project() -> None:
    """Healthy majors cross both heuristic floors effortlessly."""
    assert evaluate_community_admission(_community(reddit=50_000, telegram=10_000)) is True


def test_admission_accepts_telegram_only_project() -> None:
    """Operators may lean on Telegram even when Reddit counters read zero."""
    assert evaluate_community_admission(_community(reddit=0, telegram=20_000)) is True


def test_admission_admits_on_degraded_data() -> None:
    """Preserve admission when enrichment cannot reach CoinGecko."""
    degraded = CommunityData(
        asset_symbol="NOPE",
        twitter_followers=None,
        reddit_subscribers=None,
        reddit_average_posts_48h=None,
        reddit_average_comments_48h=None,
        reddit_accounts_active_48h=None,
        telegram_channel_user_count=None,
        last_updated_utc=datetime.now(timezone.utc),
        status="degraded",
    )

    with patch("atlas.universe.admission_filter.logger.warning") as mock_warning:
        admitted = evaluate_community_admission(degraded)

    assert admitted is True
    mock_warning.assert_called_once()
    fmt = mock_warning.call_args[0][0]
    assert "universe_admission_community_degraded" in fmt
