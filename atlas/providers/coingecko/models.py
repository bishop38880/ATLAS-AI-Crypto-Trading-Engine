"""Frozen Pydantic models for CoinGecko fundamentals payloads."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CommunityData(BaseModel):
    """CoinGecko community engagement snapshot. Fundamentals layer only."""

    model_config = ConfigDict(frozen=True)

    asset_symbol: str = Field(description="Trading base or CoinGecko context symbol for this snapshot.")
    twitter_followers: int | None = Field(description="Absolute Twitter follower count.")
    reddit_subscribers: int | None = Field(description="Absolute Reddit subscriber count.")
    reddit_average_posts_48h: float | None = Field(description="Reddit rolling average posts / 48h.")
    reddit_average_comments_48h: float | None = Field(
        description="Reddit rolling average comments / 48h.",
    )
    reddit_accounts_active_48h: float | None = Field(description="Reddit rolling active accounts / 48h.")
    telegram_channel_user_count: int | None = Field(description="Telegram channel user count.")
    last_updated_utc: datetime = Field(description="Observation timestamp (UTC).")
    status: Literal["healthy", "degraded"] = Field(description="Adapter health marker for this row.")
