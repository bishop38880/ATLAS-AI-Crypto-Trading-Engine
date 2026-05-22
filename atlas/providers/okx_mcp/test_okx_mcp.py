"""OKX MCP provider tests — unit + smoke tests.

Tests: instId normalisation, model parsing, health status.
"""

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from atlas.providers.okx_mcp.connector import (
    OKXMCPConnector,
    normalise_to_okx_instid,
)
from atlas.providers.okx_mcp.models import (
    OKXFundingHistory,
    OKXFundingRate,
    OKXFundingRateBar,
    OKXLongShortRatio,
    OKXOpenInterest,
)


# ── instId normalisation ────────────────────────────────────


def test_normalise_btc_to_okx_instid():
    """BTC normalises to BTC-USDT-SWAP."""
    assert normalise_to_okx_instid("BTCUSDT") == "BTC-USDT-SWAP"


def test_normalise_1inch_edge_case():
    """1INCH (starts with digit) normalises correctly."""
    assert normalise_to_okx_instid("1INCHUSDT") == "1INCH-USDT-SWAP"


def test_normalise_render():
    """RENDER normalises correctly."""
    assert normalise_to_okx_instid("RENDERUSDT") == "RENDER-USDT-SWAP"


def test_normalise_lowercase_input():
    """Lowercase input normalises correctly."""
    assert normalise_to_okx_instid("btcusdt") == "BTC-USDT-SWAP"


# ── Model parsing ───────────────────────────────────────────


def test_funding_rate_model_parses_decimal():
    """Funding rate field is Decimal, not float."""
    rate = OKXFundingRate(
        inst_id="BTC-USDT-SWAP",
        funding_rate=Decimal("0.0001"),
        funding_time=1700000000000,
        next_funding_time=1700028800000,
        min_funding_rate=Decimal("-0.00375"),
        max_funding_rate=Decimal("0.00375"),
        fetched_at_ms=1700000000000,
    )
    assert isinstance(rate.funding_rate, Decimal)
    assert rate.funding_rate == Decimal("0.0001")


def test_funding_history_model_90_bars():
    """History model holds 90 bars."""
    bars = [
        OKXFundingRateBar(
            inst_id="BTC-USDT-SWAP",
            funding_rate=Decimal(f"0.000{i % 10}"),
            funding_time=1700000000000 + i * 28800000,
            realized_rate=Decimal(f"0.000{i % 10}"),
        )
        for i in range(90)
    ]
    history = OKXFundingHistory(inst_id="BTC-USDT-SWAP", bars=bars)
    assert len(history.bars) == 90


def test_oi_model_parses_correctly():
    """OI field is Decimal."""
    oi = OKXOpenInterest(
        inst_id="BTC-USDT-SWAP",
        oi=Decimal("45000.5"),
        oi_ccy=Decimal("2700030000"),
        ts=1700000000000,
    )
    assert isinstance(oi.oi, Decimal)
    assert oi.oi == Decimal("45000.5")


def test_ls_ratio_sums_to_one():
    """Long ratio + short ratio = 1.0."""
    ratio = OKXLongShortRatio(
        inst_id="BTC-USDT-SWAP",
        long_short_ratio=Decimal("1.5"),
        long_ratio=Decimal("0.6"),
        short_ratio=Decimal("0.4"),
        ts=1700000000000,
    )
    assert ratio.long_ratio + ratio.short_ratio == Decimal("1.0")


def test_funding_rate_model_is_frozen():
    """Frozen model cannot be mutated."""
    rate = OKXFundingRate(
        inst_id="BTC-USDT-SWAP",
        funding_rate=Decimal("0.0001"),
        funding_time=1700000000000,
        next_funding_time=1700028800000,
        min_funding_rate=Decimal("-0.00375"),
        max_funding_rate=Decimal("0.00375"),
        fetched_at_ms=1700000000000,
    )
    with pytest.raises(Exception):
        rate.funding_rate = Decimal("0.001")  # type: ignore[misc]


# ── Health status ────────────────────────────────────────────


def test_provider_returns_healthy_on_no_errors():
    """Fresh connector reports healthy."""
    connector = OKXMCPConnector("https://mcp.okx.com")
    health = connector.get_health()
    assert health["status"] == "healthy"
    assert health["error_count"] == 0


@pytest.mark.asyncio
async def test_provider_degrades_on_mcp_timeout():
    """Timeout sets error count > 0 → degraded."""
    connector = OKXMCPConnector("https://mcp.okx.com")

    async def _timeout_tool(name, args):
        raise asyncio.TimeoutError("MCP timeout")

    connector._call_mcp_tool = _timeout_tool  # type: ignore[assignment]
    result = await connector.fetch_funding_rate("BTC-USDT-SWAP")
    assert result is None
    health = connector.get_health()
    assert health["status"] == "degraded"
    assert health["error_count"] == 1
