"""Tests for Pyth vs CoinGecko spot cross-source consistency."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock

import msgspec
import pytest

from atlas.core.consistency_checker import ConsistencyChecker
from atlas.core.cross_source_spot_price import (
    read_pyth_spot_price_usd,
    spot_price_consistency_pyth_coingecko,
)


@pytest.mark.asyncio
async def test_read_pyth_spot_price_usd_parses_redis() -> None:
    redis = AsyncMock()
    redis.get = AsyncMock(
        return_value=msgspec.json.encode({"price": "50000.5", "conf": "1", "publish_time": 1}),
    )
    px = await read_pyth_spot_price_usd(redis, "BTC")
    assert px == Decimal("50000.5")


@pytest.mark.asyncio
async def test_read_pyth_spot_price_usd_missing_returns_none() -> None:
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    assert await read_pyth_spot_price_usd(redis, "BTC") is None


@pytest.mark.asyncio
async def test_spot_price_consistency_aligned_sources() -> None:
    redis = AsyncMock()
    redis.get = AsyncMock(
        return_value=msgspec.json.encode({"price": "100", "conf": "0.1", "publish_time": 1}),
    )
    checker = ConsistencyChecker()
    res = await spot_price_consistency_pyth_coingecko(
        redis,
        "BTC",
        Decimal("100"),
        checker,
    )
    assert res is not None
    assert res.is_consistent
    assert res.max_divergence_pct == 0.0


@pytest.mark.asyncio
async def test_spot_price_consistency_divergent() -> None:
    redis = AsyncMock()
    redis.get = AsyncMock(
        return_value=msgspec.json.encode({"price": "100", "conf": "0.1", "publish_time": 1}),
    )
    checker = ConsistencyChecker()
    res = await spot_price_consistency_pyth_coingecko(
        redis,
        "BTC",
        Decimal("101"),
        checker,
    )
    assert res is not None
    assert not res.is_consistent
    assert res.max_divergence_pct > 0.5
