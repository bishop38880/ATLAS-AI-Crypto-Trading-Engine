"""Derivatives feed coordinator tests.

Tests: primary source preference, fallback, total failure, parallelism.
"""

import asyncio
import time
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from atlas.providers.derivatives_feed import DerivativesBundle, DerivativesFeed


def _mock_okx_provider(
    funding_rate=None, funding_history=None,
    oi=None, oi_history=None, ls=None, liqs=None,
):
    """Create mock OKX provider with configurable returns."""
    mock = MagicMock()
    mock.fetch_funding_rate = AsyncMock(return_value=funding_rate)
    mock.fetch_funding_history = AsyncMock(return_value=funding_history)
    mock.fetch_open_interest = AsyncMock(return_value=oi)
    mock.fetch_oi_history = AsyncMock(return_value=oi_history)
    mock.fetch_long_short_ratio = AsyncMock(return_value=ls)
    mock.fetch_liquidations = AsyncMock(return_value=liqs)
    return mock


def _mock_cg_provider(
    funding_current=None, funding_history=None,
    oi_history=None, ls=None, liqs=None,
):
    """Create mock Coinalyze provider."""
    mock = MagicMock()
    mock.fetch_funding_current = AsyncMock(return_value=funding_current)
    mock.fetch_funding_history = AsyncMock(return_value=funding_history)
    mock.fetch_oi_history = AsyncMock(return_value=oi_history)
    mock.fetch_long_short_ratio = AsyncMock(return_value=ls)
    mock.fetch_liquidation_history = AsyncMock(return_value=liqs)
    return mock


def _make_okx_funding_rate():
    """Create a mock OKX funding rate result."""
    mock = MagicMock()
    mock.funding_rate = Decimal("0.0001")
    return mock


def _make_cg_funding_bar():
    """Create a mock CG funding bar."""
    mock = MagicMock()
    mock.funding_rate = Decimal("0.0002")
    return mock


@pytest.mark.asyncio
async def test_fetch_all_uses_okx_primary():
    """OKX MCP is primary source when available."""
    okx = _mock_okx_provider(funding_rate=_make_okx_funding_rate())
    cg = _mock_cg_provider()
    feed = DerivativesFeed(okx_provider=okx, cg_provider=cg)

    rate, source = await feed.fetch_funding_rate("BTC")
    assert rate == Decimal("0.0001")
    assert source == "okx_mcp"


@pytest.mark.asyncio
async def test_fetch_funding_falls_back_to_coinalyze():
    """Falls back to Coinalyze when OKX fails."""
    okx = _mock_okx_provider()
    okx.fetch_funding_rate = AsyncMock(side_effect=Exception("MCP down"))
    cg = _mock_cg_provider(funding_current=_make_cg_funding_bar())
    feed = DerivativesFeed(okx_provider=okx, cg_provider=cg)

    rate, source = await feed.fetch_funding_rate("BTC")
    assert rate == Decimal("0.0002")
    assert source == "coinalyze_v3"


@pytest.mark.asyncio
async def test_fetch_all_returns_bundle_with_none_on_total_failure():
    """All tiers failing returns bundle with None fields."""
    okx = _mock_okx_provider()
    okx.fetch_funding_rate = AsyncMock(side_effect=Exception("fail"))
    okx.fetch_funding_history = AsyncMock(side_effect=Exception("fail"))
    okx.fetch_open_interest = AsyncMock(side_effect=Exception("fail"))
    okx.fetch_oi_history = AsyncMock(side_effect=Exception("fail"))
    okx.fetch_long_short_ratio = AsyncMock(side_effect=Exception("fail"))
    okx.fetch_liquidations = AsyncMock(side_effect=Exception("fail"))
    cg = _mock_cg_provider()
    cg.fetch_funding_current = AsyncMock(side_effect=Exception("fail"))
    cg.fetch_funding_history = AsyncMock(side_effect=Exception("fail"))
    cg.fetch_oi_history = AsyncMock(side_effect=Exception("fail"))
    cg.fetch_long_short_ratio = AsyncMock(side_effect=Exception("fail"))
    cg.fetch_liquidation_history = AsyncMock(side_effect=Exception("fail"))

    feed = DerivativesFeed(okx_provider=okx, cg_provider=cg)
    bundle = await feed.fetch_all("BTC")

    assert isinstance(bundle, DerivativesBundle)
    assert bundle.funding_rate is None
    assert bundle.funding_history is None


@pytest.mark.asyncio
async def test_fetch_all_parallel_not_sequential():
    """fetch_all runs in parallel, not sequentially."""
    async def _slow_funding(*args, **kwargs):
        await asyncio.sleep(0.05)
        return _make_okx_funding_rate()

    async def _slow_none(*args, **kwargs):
        await asyncio.sleep(0.05)
        return None

    okx = _mock_okx_provider()
    okx.fetch_funding_rate = _slow_funding
    okx.fetch_funding_history = _slow_none
    okx.fetch_open_interest = _slow_none
    okx.fetch_oi_history = _slow_none
    okx.fetch_long_short_ratio = _slow_none
    okx.fetch_liquidations = _slow_none

    feed = DerivativesFeed(okx_provider=okx, cg_provider=None)
    start = time.monotonic()
    bundle = await feed.fetch_all("BTC")
    elapsed = time.monotonic() - start

    # 6 calls × 50ms each = 300ms sequential. Parallel < 200ms.
    assert elapsed < 0.25
    assert bundle.funding_rate == Decimal("0.0001")


@pytest.mark.asyncio
async def test_fetch_all_no_providers():
    """No providers returns empty bundle."""
    feed = DerivativesFeed(okx_provider=None, cg_provider=None)
    bundle = await feed.fetch_all("BTC")
    assert isinstance(bundle, DerivativesBundle)
    assert bundle.funding_rate is None
    assert bundle.asset == "BTC"


@pytest.mark.asyncio
async def test_derivatives_bundle_is_frozen():
    """DerivativesBundle cannot be mutated."""
    bundle = DerivativesBundle(
        asset="BTC",
        funding_rate=Decimal("0.0001"),
        fetched_at_ms=1700000000000,
    )
    with pytest.raises(Exception):
        bundle.funding_rate = Decimal("0.001")  # type: ignore[misc]
