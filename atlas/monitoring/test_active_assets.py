"""Tests for daily-8 asset resolution."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from atlas.monitoring.active_assets import fallback_daily_8_bases, resolve_daily_8_asset_bases


class TestActiveAssets:
    def test_fallback_returns_eight_bases(self) -> None:
        bases = fallback_daily_8_bases()
        assert len(bases) == 8
        assert bases[0] == "BTC"

    @pytest.mark.asyncio
    async def test_resolve_from_redis(self) -> None:
        redis = AsyncMock()
        redis.smembers = AsyncMock(return_value={b"ETHUSDT", b"BTCUSDT"})
        bases = await resolve_daily_8_asset_bases(redis)
        assert bases == ["BTC", "ETH"]
