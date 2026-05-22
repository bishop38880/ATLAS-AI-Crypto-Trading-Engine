"""Test suite for FREDProvider — Tier 2 FRED REST connector.

Covers:
  1. Graceful degradation without API key (OFFLINE, not crash)
  2. Successful macro snapshot fetch
  3. RISK_ON / RISK_OFF regime classification
  4. Network timeout resilience
  5. fetch_data() returns Pydantic object (not dict)
  6. Decimal precision enforcement
  7. Frozen model immutability
  8. Cache miss path
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import httpx
import msgspec
import pytest

from atlas.providers.fred.connector import FREDProvider, _safe_decimal
from atlas.providers.fred.models import MacroSnapshot, YieldCurveData


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

@pytest.fixture
def mock_settings() -> MagicMock:
    """Settings with a valid FRED API key."""
    s = MagicMock()
    s.fred_api_key.get_secret_value.return_value = "mock_key_123"
    s.fred_base_url = "https://api.stlouisfed.org"
    s.fred_ttl_seconds = 300
    return s


@pytest.fixture
def mock_settings_no_key() -> MagicMock:
    """Settings with empty FRED API key."""
    s = MagicMock()
    s.fred_api_key.get_secret_value.return_value = ""
    s.fred_base_url = "https://api.stlouisfed.org"
    s.fred_ttl_seconds = 300
    return s


@pytest.fixture
def redis_client() -> AsyncMock:
    """Mock Redis client with cache miss."""
    r = AsyncMock()
    r.get.return_value = None
    return r


def _make_fred_response(series_id: str) -> Mock:
    """Build a mock httpx.Response for a FRED series."""
    data_map = {
        "FEDFUNDS": {"observations": [{"value": "4.5"}]},
        "DGS10": {"observations": [{"value": "3.5"}]},
        "DGS2": {"observations": [{"value": "4.0"}]},
        "CPIAUCSL": {"observations": [{"value": "3.2"}]},
    }
    resp = Mock(spec=httpx.Response)
    resp.raise_for_status = Mock()
    resp.content = msgspec.json.encode(data_map.get(series_id, {"observations": []}))
    return resp


@pytest.fixture
def mock_http_client() -> AsyncMock:
    """Mock httpx.AsyncClient returning FRED series data via content bytes."""
    client = AsyncMock(spec=httpx.AsyncClient)

    async def get_mock(*args: object, **kwargs: object) -> Mock:
        params = kwargs.get("params", {})
        if isinstance(params, dict):
            series_id = params.get("series_id", "")
        else:
            series_id = ""
        return _make_fred_response(str(series_id))

    client.get = get_mock  # type: ignore[assignment]
    return client


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fred_missing_api_key_graceful_degradation(
    redis_client: AsyncMock,
    mock_settings_no_key: MagicMock,
    mock_http_client: AsyncMock,
) -> None:
    """FRED must NOT raise ValueError with missing key — must degrade to OFFLINE."""
    provider = FREDProvider(redis_client, mock_settings_no_key, mock_http_client)
    assert provider.status == "OFFLINE"

    snapshot = await provider.fetch_macro_snapshot()
    assert snapshot.stale is True
    assert snapshot.status == "degraded"
    assert snapshot.macro_regime == "NEUTRAL"
    # Verify no crash — this is the key assertion
    assert isinstance(snapshot, MacroSnapshot)


@pytest.mark.asyncio
async def test_fred_macro_snapshot_success(
    redis_client: AsyncMock,
    mock_settings: MagicMock,
    mock_http_client: AsyncMock,
) -> None:
    """Successful fetch must return populated snapshot with correct Decimals."""
    provider = FREDProvider(redis_client, mock_settings, mock_http_client)
    snapshot = await provider.fetch_macro_snapshot()

    assert snapshot.stale is False
    assert snapshot.status == "healthy"
    assert snapshot.fed_funds_rate == Decimal("4.5")
    assert snapshot.yield_curve.ten_year == Decimal("3.5")
    assert snapshot.yield_curve.two_year == Decimal("4.0")
    assert snapshot.yield_curve.spread == Decimal("-0.5")  # 3.5 - 4.0
    # RISK_OFF logic: spread < 0 AND fed_funds > 4.0
    assert snapshot.macro_regime == "RISK_OFF"


@pytest.mark.asyncio
async def test_fred_risk_on_regime() -> None:
    """RISK_ON: spread > 0.5 AND fed_funds < 3.0."""
    snapshot = MacroSnapshot(
        fed_funds_rate=Decimal("2.5"),
        yield_curve=YieldCurveData(
            ten_year=Decimal("4.0"),
            two_year=Decimal("3.0"),
            spread=Decimal("1.0"),
        ),
    )
    assert snapshot.macro_regime == "RISK_ON"


@pytest.mark.asyncio
async def test_fetch_data_returns_pydantic(
    redis_client: AsyncMock,
    mock_settings: MagicMock,
    mock_http_client: AsyncMock,
) -> None:
    """fetch_data() must return MacroSnapshot, NOT dict."""
    provider = FREDProvider(redis_client, mock_settings, mock_http_client)
    result = await provider.fetch_data()

    assert isinstance(result, MacroSnapshot)
    # Must support dot notation
    assert hasattr(result, "fed_funds_rate")
    assert hasattr(result, "yield_curve")
    assert hasattr(result, "macro_regime")


@pytest.mark.asyncio
async def test_decimal_precision_enforcement() -> None:
    """All financial fields must be Decimal, not float."""
    snapshot = MacroSnapshot(
        fed_funds_rate=Decimal("5.25"),
        cpi_yoy=Decimal("3.1"),
        stablecoin_total_supply_usd=Decimal("150000000000"),
    )
    assert isinstance(snapshot.fed_funds_rate, Decimal)
    assert isinstance(snapshot.cpi_yoy, Decimal)
    assert isinstance(snapshot.stablecoin_total_supply_usd, Decimal)


@pytest.mark.asyncio
async def test_frozen_model_immutability() -> None:
    """Frozen models must reject attribute mutation."""
    snapshot = MacroSnapshot()
    with pytest.raises(Exception):
        snapshot.fed_funds_rate = Decimal("0")  # type: ignore[misc]


@pytest.mark.asyncio
async def test_network_timeout_resilience(
    redis_client: AsyncMock,
    mock_settings: MagicMock,
) -> None:
    """Network timeout must not crash — must degrade gracefully."""
    http_client = AsyncMock(spec=httpx.AsyncClient)
    http_client.get.side_effect = httpx.TimeoutException("timeout")

    provider = FREDProvider(redis_client, mock_settings, http_client)
    snapshot = await provider.fetch_macro_snapshot()

    # Should return stale/degraded, NOT crash
    assert snapshot.stale is True
    assert isinstance(snapshot, MacroSnapshot)


@pytest.mark.asyncio
async def test_safe_decimal_edge_cases() -> None:
    """_safe_decimal must handle None, dots, and invalid strings."""
    assert _safe_decimal(None) == Decimal("0")
    assert _safe_decimal(".") == Decimal("0")
    assert _safe_decimal("not_a_number") == Decimal("0")
    assert _safe_decimal("123.45") == Decimal("123.45")
    assert _safe_decimal(42) == Decimal("42")


@pytest.mark.asyncio
async def test_health_status(
    redis_client: AsyncMock,
    mock_settings: MagicMock,
    mock_http_client: AsyncMock,
) -> None:
    """get_health_status() must return ProviderHealth."""
    provider = FREDProvider(redis_client, mock_settings, mock_http_client)
    health = await provider.get_health_status()
    assert health.name == "fred_rest"
    assert health.status == "HEALTHY"


@pytest.mark.asyncio
async def test_offline_health_status(
    redis_client: AsyncMock,
    mock_settings_no_key: MagicMock,
    mock_http_client: AsyncMock,
) -> None:
    """Provider without key must report OFFLINE health."""
    provider = FREDProvider(redis_client, mock_settings_no_key, mock_http_client)
    health = await provider.get_health_status()
    assert health.status == "OFFLINE"
