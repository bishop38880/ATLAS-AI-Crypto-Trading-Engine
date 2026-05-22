"""Universe admission gates independent of scoring overlays."""

from __future__ import annotations

from loguru import logger

from atlas.providers.coingecko.models import CommunityData

MIN_REDDIT_SUBSCRIBERS_FOR_ADMISSION = 5000
MIN_TELEGRAM_USERS_FOR_ADMISSION = 1000


def evaluate_community_admission(community: CommunityData) -> bool:
    """
    Hard community-size gate enforced only during admission refresh cadence.

    When CoinGecko is unreachable the payload arrives degraded — we quietly
    permit admission with operator visibility only (upstream warned elsewhere).

    Healthy rows require EITHER meaningful Reddit breadth OR Telegram reach.
    """
    reddit_floor = MIN_REDDIT_SUBSCRIBERS_FOR_ADMISSION
    telegram_floor = MIN_TELEGRAM_USERS_FOR_ADMISSION

    if community.status == "degraded":
        logger.warning(
            "universe_admission_community_degraded | asset={}",
            community.asset_symbol,
        )
        return True

    reddit_count = community.reddit_subscribers or 0
    telegram_members = community.telegram_channel_user_count or 0

    passes_reddit = reddit_count >= reddit_floor
    passes_telegram = telegram_members >= telegram_floor
    admitted = passes_reddit or passes_telegram
    if not admitted:
        logger.info(
            "universe_admission_community_denied | asset={} | reddit={} | telegram={}",
            community.asset_symbol,
            reddit_count,
            telegram_members,
        )
    return admitted
