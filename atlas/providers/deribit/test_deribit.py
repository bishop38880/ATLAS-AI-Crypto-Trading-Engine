"""Tests for Deribit options provider and intelligence calculations."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, Mock

import httpx
import msgspec
import pytest

from atlas.providers.deribit.chain_builder import build_chain_snapshot_from_book_summaries
from atlas.providers.deribit.connector import DeribitOptionsProvider
from atlas.providers.deribit.intelligence import (
    calculate_max_pain_price,
    calculate_options_intelligence,
    calculate_put_call_ratios,
)
from atlas.providers.deribit.models import OptionsChainSnapshot


def _mock_chain_snapshot() -> OptionsChainSnapshot:
    """Deterministic chain: max pain should land at strike 100."""
    return OptionsChainSnapshot(
        asset="BTC",
        expiry="ALL",
        strikes=[
            Decimal("90"),
            Decimal("100"),
            Decimal("110"),
        ],
        call_oi=[Decimal("10"), Decimal("50"), Decimal("5")],
        put_oi=[Decimal("5"), Decimal("20"), Decimal("30")],
        call_volume_24h=[Decimal("1"), Decimal("4"), Decimal("1")],
        put_volume_24h=[Decimal("3"), Decimal("6"), Decimal("9")],
        iv_call=[Decimal("0.5"), Decimal("0.55"), Decimal("0.6")],
        iv_put=[Decimal("0.6"), Decimal("0.58"), Decimal("0.62")],
        spot_price=Decimal("105"),
        timestamp_utc="2026-05-20T12:00:00+00:00",
    )


class TestCalculateMaxPain:
    def test_max_pain_matches_minimum_loss_strike(self) -> None:
        snapshot = _mock_chain_snapshot()
        result = calculate_max_pain_price(
            snapshot.strikes,
            snapshot.call_oi,
            snapshot.put_oi,
        )
        assert result == Decimal("100")

    def test_max_pain_is_deterministic(self) -> None:
        snapshot = _mock_chain_snapshot()
        first = calculate_max_pain_price(
            snapshot.strikes, snapshot.call_oi, snapshot.put_oi,
        )
        second = calculate_max_pain_price(
            snapshot.strikes, snapshot.call_oi, snapshot.put_oi,
        )
        assert first == second


class TestPutCallRatio:
    def test_put_call_ratio_from_mock_oi(self) -> None:
        snapshot = _mock_chain_snapshot()
        vol_pcr, oi_pcr = calculate_put_call_ratios(
            snapshot.call_volume_24h,
            snapshot.put_volume_24h,
            snapshot.call_oi,
            snapshot.put_oi,
        )
        assert vol_pcr == Decimal("18") / Decimal("6")
        assert oi_pcr == Decimal("55") / Decimal("65")


class TestComputeIntelligence:
    def test_compute_intelligence_populates_fields(self) -> None:
        snapshot = _mock_chain_snapshot()
        intel = calculate_options_intelligence(snapshot)
        assert intel.max_pain_price == Decimal("100")
        assert intel.put_call_ratio_volume > 0
        assert intel.put_call_ratio_oi > 0


@pytest.fixture
def mock_settings() -> MagicMock:
    settings = MagicMock()
    settings.deribit_options_base_url = "https://www.deribit.com/api/v2/public"
    settings.deribit_options_ttl_seconds = 300
    settings.deribit_options_timeout_seconds = 10.0
    return settings


@pytest.fixture
def redis_client() -> AsyncMock:
    client = AsyncMock()
    client.get.return_value = None
    return client


@pytest.mark.asyncio
async def test_provider_initialises_and_health_check(
    redis_client: AsyncMock,
    mock_settings: MagicMock,
) -> None:
    http_client = AsyncMock(spec=httpx.AsyncClient)
    response = Mock(spec=httpx.Response)
    response.raise_for_status = Mock()
    response.content = msgspec.json.encode(
        {"jsonrpc": "2.0", "result": 1_700_000_000_000},
    )
    http_client.get.return_value = response

    provider = DeribitOptionsProvider(redis_client, mock_settings, http_client)
    assert provider.get_tier() == 2
    assert await provider.health_check() is True
    health = await provider.get_health_status()
    assert health.name == "deribit_options"


def test_build_chain_snapshot_from_book_rows() -> None:
    summaries = [
        {
            "instrument_name": "BTC-28MAR25-100000-C",
            "open_interest": 10,
            "volume": 2,
            "mark_iv": 0.55,
            "underlying_price": 105000,
        },
        {
            "instrument_name": "BTC-28MAR25-100000-P",
            "open_interest": 20,
            "volume": 4,
            "mark_iv": 0.58,
            "underlying_price": 105000,
        },
    ]
    snapshot = build_chain_snapshot_from_book_summaries("BTC", summaries)
    assert snapshot.asset == "BTC"
    assert Decimal("100000") in snapshot.strikes
    assert sum(snapshot.call_oi) == Decimal("10")
    assert sum(snapshot.put_oi) == Decimal("20")
