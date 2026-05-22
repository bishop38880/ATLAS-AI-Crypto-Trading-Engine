"""Tests for altFINS Provider Adapter."""

import msgspec
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import redis.asyncio as redis_async

from atlas.providers.altfins.adapter import (
    ALTFINS_SIGNALS_TTL,
    ALTFINS_SUMMARY_TTL,
    SUPPORTED_TIMEFRAMES,
    AltFinsAdapter,
    CoinTechnicalSummary,
    TechnicalSignalsData,
)


def _make_mock_redis() -> AsyncMock:
    """Create a properly-configured async Redis mock."""
    client = AsyncMock()
    client.get = AsyncMock(return_value=None)
    client.setex = AsyncMock(return_value=None)
    return client


def _make_signals_payload(asset: str) -> dict:
    """Build a valid signals JSON payload for testing."""
    return {
        "asset": asset,
        "status": "healthy",
        "timeframes": {
            tf: {
                "timeframe": tf,
                "trend_direction": "bullish",
                "signal_count": 3,
                "indicator_agreement_pct": 80.0,
                "top_signals": ["MACD", "RSI"],
                "raw_score": 85.0,
                "status": "healthy",
            }
            for tf in SUPPORTED_TIMEFRAMES
        },
    }


def _make_summary_payload(asset: str) -> dict:
    """Build a valid coin summary JSON payload for testing."""
    return {
        "asset": asset,
        "overall_signal": "bullish",
        "overall_score": 75.5,
        "patterns_detected": ["Double Bottom"],
        "support_levels": [200.0, 190.0],
        "resistance_levels": [250.0, 260.0],
        "rsi_14": 65.0,
        "macd_signal": "buy",
        "volume_trend": "increasing",
        "last_updated_utc": datetime.now(timezone.utc).isoformat(),
        "status": "healthy",
    }


@pytest.fixture
def mock_redis():
    """Return a mock Redis client with async-safe methods."""
    return _make_mock_redis()


@pytest.fixture
def adapter(mock_redis):
    """Return an AltFinsAdapter instance with mock Redis."""
    return AltFinsAdapter(redis_client=mock_redis)


# ── Test 1 ────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_fetch_technical_signals_returns_all_5_timeframes(adapter):
    """Mock httpx. Assert TechnicalSignalsData.timeframes has all 5 keys."""
    asset = "BTC"
    payload = _make_signals_payload(asset)

    mock_response = httpx.Response(
        200,
        json=payload,
        request=httpx.Request("GET", f"https://api.altfins.com/api/v1/signals/{asset}"),
    )
    adapter.client = AsyncMock()
    adapter.client.get = AsyncMock(return_value=mock_response)

    result = await adapter.fetch_technical_signals(asset)

    assert isinstance(result, TechnicalSignalsData)
    assert result.asset == asset
    assert result.status == "healthy"
    assert set(result.timeframes.keys()) == set(SUPPORTED_TIMEFRAMES)


# ── Test 2 ────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_fetch_technical_signals_cache_hit_skips_api(adapter, mock_redis):
    """Seed Redis mock. Assert httpx never called."""
    asset = "ETH"
    payload = _make_signals_payload(asset)

    import json
    mock_redis.get = AsyncMock(return_value=json.dumps(payload))

    adapter.client = AsyncMock()
    adapter.client.get = AsyncMock()

    result = await adapter.fetch_technical_signals(asset)

    assert result.asset == asset
    adapter.client.get.assert_not_called()


# ── Test 3 ────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_fetch_technical_signals_degraded_on_api_failure(adapter):
    """Mock httpx to raise. Assert status=degraded, no raise, 0.0 values."""
    asset = "SOL"

    adapter.client = AsyncMock()
    adapter.client.get = AsyncMock(
        side_effect=httpx.ConnectError("API Down"),
    )

    result = await adapter.fetch_technical_signals(asset)

    assert result.status == "degraded"
    assert set(result.timeframes.keys()) == set(SUPPORTED_TIMEFRAMES)
    for tf_sig in result.timeframes.values():
        assert tf_sig.status == "degraded"
        assert tf_sig.raw_score == 0.0
        assert tf_sig.indicator_agreement_pct == 0.0

    assert adapter.status == "DEGRADED"


# ── Test 4 ────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_fetch_coin_summary_returns_valid_fields(adapter):
    """Mock httpx. Assert all CoinTechnicalSummary fields populated."""
    asset = "BNB"
    payload = _make_summary_payload(asset)

    mock_response = httpx.Response(
        200,
        json=payload,
        request=httpx.Request("GET", f"https://api.altfins.com/api/v1/summary/{asset}"),
    )
    adapter.client = AsyncMock()
    adapter.client.get = AsyncMock(return_value=mock_response)

    result = await adapter.fetch_coin_summary(asset)

    assert isinstance(result, CoinTechnicalSummary)
    assert result.asset == asset
    assert result.rsi_14 == 65.0
    assert result.status == "healthy"
    assert result.macd_signal == "buy"


# ── Test 5 ────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_fetch_coin_summary_degraded_on_api_failure(adapter):
    """Mock httpx to raise. Assert status=degraded, no raise."""
    asset = "ADA"

    adapter.client = AsyncMock()
    adapter.client.get = AsyncMock(
        side_effect=httpx.ConnectError("Connection refused"),
    )

    result = await adapter.fetch_coin_summary(asset)

    assert result.status == "degraded"
    assert result.rsi_14 == 0.0
    assert result.overall_score == 0.0
    assert adapter.status == "DEGRADED"


# ── Test 6 ────────────────────────────────────────────────────
def test_calculate_empty_fallback_signals_is_structurally_valid(adapter):
    """Call fallback directly. Assert model validates, all numerics == 0.0."""
    result = adapter.calculate_empty_fallback_signals("BTC")

    assert isinstance(result, TechnicalSignalsData)
    assert result.status == "degraded"
    for tf in SUPPORTED_TIMEFRAMES:
        assert tf in result.timeframes
        sig = result.timeframes[tf]
        assert sig.raw_score == 0.0
        assert sig.indicator_agreement_pct == 0.0
        assert sig.signal_count == 0
        assert sig.top_signals == []
        assert sig.status == "degraded"


# ── Test 7 ────────────────────────────────────────────────────
def test_altfins_ttl_constants_are_30_seconds():
    """Assert TTL constants are exactly 30."""
    assert ALTFINS_SIGNALS_TTL == 30
    assert ALTFINS_SUMMARY_TTL == 30


# ── Test 8 (Smoke) ───────────────────────────────────────────
@pytest.mark.asyncio
async def test_adapter_registers_and_returns_healthy(adapter):
    """Instantiate adapter, mock ping, assert healthy status."""
    mock_response = httpx.Response(
        200,
        request=httpx.Request("GET", "https://api.altfins.com/ping"),
    )
    adapter.client = AsyncMock()
    adapter.client.get = AsyncMock(return_value=mock_response)

    health = await adapter.get_health_status()

    assert health.name == "altfins"
    assert health.status == "HEALTHY"
