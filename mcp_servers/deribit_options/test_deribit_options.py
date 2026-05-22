"""Validation suite for Deribit Options Flow & Volatility Surface MCP.

Section 26.3 Architecture — Options Skew Detector.
17-test suite covering:
  - State Manager (5 tests)
  - Quant Engine (8 tests)
  - Server Integration (4 tests)

Sentinel Invariants:
  - Tests use pytest + pytest-asyncio (approved stack)
  - No stdlib json, no pandas, no os.getenv
  - Decimal for financial assertions
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from mcp_servers.deribit_options.state_manager import DeribitStateCache
from mcp_servers.deribit_options.quant_engine import (
    calculate_options_skew,
    calculate_iv_surface,
    calculate_put_call_ratio,
    calculate_block_trades,
    _interpolate_25d_iv,
    _classify_skew,
    _find_atm_iv,
    _safe_ratio,
    _aggregate_volume_oi,
)
from mcp_servers.deribit_options.models import (
    OptionsSkewResponse,
    IVSurfaceResponse,
    PutCallRatioResponse,
    BlockTradesResponse,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_ticker(
    instrument: str,
    mark_iv: float = 0.5,
    delta: float = 0.5,
    volume: float = 100.0,
    oi: float = 1000.0,
    underlying_price: float = 60000.0,
) -> dict:
    """Build a mock Deribit ticker payload.

    Args:
        instrument: Deribit instrument name.
        mark_iv: Mark implied volatility.
        delta: Option delta.
        volume: 24h volume.
        oi: Open interest.
        underlying_price: Underlying index price.

    Returns:
        Mock ticker dict mimicking Deribit payload.
    """
    return {
        "instrument_name": instrument,
        "mark_iv": mark_iv,
        "underlying_price": underlying_price,
        "open_interest": oi,
        "greeks": {"delta": delta, "gamma": 0.0001, "vega": 50.0},
        "stats": {"volume": volume},
    }


def _make_trade(
    instrument: str,
    amount: float = 10.0,
    price: float = 0.05,
    direction: str = "buy",
    mark_iv: float = 0.45,
    timestamp: int = 1700000000000,
) -> dict:
    """Build a mock Deribit trade payload.

    Args:
        instrument: Deribit instrument name.
        amount: Contract quantity.
        price: Execution price.
        direction: 'buy' or 'sell'.
        mark_iv: Mark IV at execution.
        timestamp: Unix timestamp in milliseconds.

    Returns:
        Mock trade dict.
    """
    return {
        "instrument_name": instrument,
        "amount": amount,
        "price": price,
        "direction": direction,
        "mark_iv": mark_iv,
        "timestamp": timestamp,
    }


def _build_populated_cache() -> DeribitStateCache:
    """Build a cache pre-populated with BTC option tickers.

    Creates a realistic set of calls and puts at various deltas
    for the 30MAY25 expiry to support interpolation tests.

    Returns:
        Populated DeribitStateCache.
    """
    cache = DeribitStateCache()
    cache.set_ws_connected(True)
    cache.update_underlying_price("BTC", Decimal("60000"))

    # Calls with varying deltas (sorted by strike ascending)
    call_specs = [
        ("BTC-30MAY25-50000-C", 0.65, 0.85),
        ("BTC-30MAY25-55000-C", 0.60, 0.55),
        ("BTC-30MAY25-58000-C", 0.55, 0.40),
        ("BTC-30MAY25-60000-C", 0.50, 0.50),  # ATM
        ("BTC-30MAY25-62000-C", 0.48, 0.28),
        ("BTC-30MAY25-65000-C", 0.52, 0.22),
        ("BTC-30MAY25-70000-C", 0.58, 0.15),
    ]
    for inst, iv, delta in call_specs:
        cache.update_ticker(inst, _make_ticker(inst, mark_iv=iv, delta=delta))

    # Puts with varying deltas
    put_specs = [
        ("BTC-30MAY25-50000-P", 0.70, -0.15),
        ("BTC-30MAY25-55000-P", 0.62, -0.22),
        ("BTC-30MAY25-58000-P", 0.55, -0.28),
        ("BTC-30MAY25-60000-P", 0.50, -0.50),  # ATM
        ("BTC-30MAY25-62000-P", 0.48, -0.55),
        ("BTC-30MAY25-65000-P", 0.52, -0.85),
        ("BTC-30MAY25-70000-P", 0.60, -0.90),
    ]
    for inst, iv, delta in put_specs:
        cache.update_ticker(inst, _make_ticker(inst, mark_iv=iv, delta=delta))

    return cache


# ===========================================================================
# State Manager Tests (5)
# ===========================================================================

class TestStateManager:
    """Tests for DeribitStateCache."""

    def test_update_ticker_stores_data(self) -> None:
        """Ticker write/read roundtrip produces correct data."""
        cache = DeribitStateCache()
        ticker = _make_ticker("BTC-30MAY25-60000-C")
        cache.update_ticker("BTC-30MAY25-60000-C", ticker)

        tickers = cache.get_tickers_for_coin("BTC")
        assert len(tickers) == 1
        assert tickers[0]["instrument_name"] == "BTC-30MAY25-60000-C"

    def test_block_trade_filtering(self) -> None:
        """Trades above the institutional threshold are captured."""
        cache = DeribitStateCache()
        cache.update_underlying_price("BTC", Decimal("60000"))

        # Trade with notional = 10 * 60000 * 0.05 = 30000 (below threshold)
        small_trade = _make_trade("BTC-30MAY25-60000-C", amount=10, price=0.05)
        cache.add_trade(small_trade)
        assert len(cache.get_block_trades("BTC")) == 0

        # Trade with notional = 1000 * 60000 * 0.1 = 6,000,000 (above threshold)
        big_trade = _make_trade("BTC-30MAY25-60000-C", amount=1000, price=0.1)
        cache.add_trade(big_trade)
        assert len(cache.get_block_trades("BTC")) == 1

    def test_block_trade_buffer_size(self) -> None:
        """Block trade buffer is capped at 50 entries."""
        cache = DeribitStateCache()
        cache.update_underlying_price("BTC", Decimal("60000"))

        for i in range(60):
            trade = _make_trade(
                "BTC-30MAY25-60000-C",
                amount=2000 + i,
                price=0.1,
            )
            cache.add_trade(trade)

        blocks = cache.get_block_trades("BTC")
        assert len(blocks) <= 50

    def test_staleness_detection(self) -> None:
        """is_stale() returns True when no data has been received."""
        cache = DeribitStateCache()
        assert cache.is_stale() is True

        cache.update_ticker("BTC-30MAY25-60000-C", _make_ticker("BTC-30MAY25-60000-C"))
        assert cache.is_stale() is False

    def test_get_tickers_filters_by_coin(self) -> None:
        """Coin prefix filtering correctly separates BTC and ETH."""
        cache = DeribitStateCache()
        cache.update_ticker("BTC-30MAY25-60000-C", _make_ticker("BTC-30MAY25-60000-C"))
        cache.update_ticker("ETH-30MAY25-3000-C", _make_ticker("ETH-30MAY25-3000-C"))

        btc_tickers = cache.get_tickers_for_coin("BTC")
        eth_tickers = cache.get_tickers_for_coin("ETH")
        assert len(btc_tickers) == 1
        assert len(eth_tickers) == 1
        assert btc_tickers[0]["instrument_name"].startswith("BTC-")
        assert eth_tickers[0]["instrument_name"].startswith("ETH-")


# ===========================================================================
# Quant Engine Tests (8)
# ===========================================================================

class TestQuantEngine:
    """Tests for quant_engine.py computation functions."""

    def test_interpolate_25d_call_iv(self) -> None:
        """Linear interpolation correctly finds 25-delta call IV.

        With deltas [0.22, 0.28] and IVs [0.52, 0.48], the
        interpolated IV at 0.25 should be the midpoint ≈ 0.50.
        """
        options = [
            {"instrument_name": "BTC-30MAY25-65000-C", "mark_iv": 0.52,
             "greeks": {"delta": 0.22}},
            {"instrument_name": "BTC-30MAY25-62000-C", "mark_iv": 0.48,
             "greeks": {"delta": 0.28}},
        ]
        result = _interpolate_25d_iv(options, target_delta=0.25)
        assert result is not None
        assert abs(result - 0.50) < 0.001

    def test_interpolate_25d_put_iv(self) -> None:
        """Linear interpolation correctly finds 25-delta put IV.

        With deltas [-0.28, -0.22] and IVs [0.55, 0.62],
        interpolated at -0.25 should be ~0.585.
        """
        options = [
            {"instrument_name": "BTC-30MAY25-58000-P", "mark_iv": 0.55,
             "greeks": {"delta": -0.28}},
            {"instrument_name": "BTC-30MAY25-55000-P", "mark_iv": 0.62,
             "greeks": {"delta": -0.22}},
        ]
        result = _interpolate_25d_iv(options, target_delta=-0.25)
        assert result is not None
        assert abs(result - 0.585) < 0.001

    def test_risk_reversal_positive_bullish(self) -> None:
        """Positive risk reversal is labeled 'Bullish Skew'."""
        assert _classify_skew(0.05) == "Bullish Skew"
        assert _classify_skew(0.03) == "Bullish Skew"

    def test_risk_reversal_negative_bearish(self) -> None:
        """Negative risk reversal is labeled 'Bearish Skew'."""
        assert _classify_skew(-0.05) == "Bearish Skew"
        assert _classify_skew(-0.03) == "Bearish Skew"

    def test_risk_reversal_neutral_band(self) -> None:
        """Near-zero risk reversal is labeled 'Neutral'."""
        assert _classify_skew(0.01) == "Neutral"
        assert _classify_skew(-0.01) == "Neutral"
        assert _classify_skew(0.0) == "Neutral"

    def test_atm_strike_selection(self) -> None:
        """Closest strike to underlying is selected as ATM."""
        tickers = [
            _make_ticker("BTC-30MAY25-55000-C", mark_iv=0.60),
            _make_ticker("BTC-30MAY25-60000-C", mark_iv=0.50),
            _make_ticker("BTC-30MAY25-65000-C", mark_iv=0.55),
        ]
        underlying = Decimal("59800")
        result = _find_atm_iv(tickers, underlying)
        assert result is not None
        # Strike 60000 is closest to 59800
        assert result == 0.50

    def test_put_call_ratio_calculation(self) -> None:
        """Volume and OI ratios are correctly computed."""
        tickers = [
            _make_ticker("BTC-30MAY25-60000-C", volume=200, oi=5000),
            _make_ticker("BTC-30MAY25-60000-P", volume=300, oi=4000),
            _make_ticker("BTC-30MAY25-65000-C", volume=100, oi=3000),
            _make_ticker("BTC-30MAY25-65000-P", volume=150, oi=2000),
        ]
        stats = _aggregate_volume_oi(tickers)
        # Calls: 200+100=300, Puts: 300+150=450 → P/C = 1.5
        assert _safe_ratio(stats["put_vol"], stats["call_vol"]) == 1.5
        # Call OI: 5000+3000=8000, Put OI: 4000+2000=6000 → P/C = 0.75
        assert _safe_ratio(stats["put_oi"], stats["call_oi"]) == 0.75

    def test_empty_tickers_returns_empty(self) -> None:
        """Graceful degradation with no ticker data."""
        cache = DeribitStateCache()
        result = calculate_options_skew(cache, "BTC")
        assert isinstance(result, OptionsSkewResponse)
        assert result.entries == []
        assert result.ws_status == "INITIALIZING"


# ===========================================================================
# Server Integration Tests (4)
# ===========================================================================

class TestServerIntegration:
    """Integration tests for MCP tool dispatch."""

    def test_tool_returns_structured_output(self) -> None:
        """Options skew tool returns a valid OptionsSkewResponse."""
        cache = _build_populated_cache()
        result = calculate_options_skew(cache, "BTC")
        assert isinstance(result, OptionsSkewResponse)
        assert result.coin == "BTC"
        assert result.ws_status == "CONNECTED"

    def test_stale_data_includes_status(self) -> None:
        """Disconnected cache reports DISCONNECTED status.

        When the WS has previously connected (last_update_ts > 0)
        but is currently disconnected, status should be 'DISCONNECTED'.
        """
        cache = DeribitStateCache()
        cache.update_ticker("BTC-30MAY25-60000-C", _make_ticker("BTC-30MAY25-60000-C"))
        cache.set_ws_connected(False)

        result = calculate_iv_surface(cache, "BTC")
        assert isinstance(result, IVSurfaceResponse)
        assert result.ws_status == "DISCONNECTED"

    def test_invalid_coin_returns_error(self) -> None:
        """Unknown coins are rejected by validate_coin."""
        from mcp_servers.deribit_options.server import _validate_coin

        with pytest.raises(ValueError, match="Unsupported coin"):
            _validate_coin("DOGE")

    def test_block_trades_notional_filter(self) -> None:
        """min_notional_usd filter is respected in block trade output."""
        cache = DeribitStateCache()
        cache.set_ws_connected(True)
        cache.update_underlying_price("BTC", Decimal("60000"))

        # Add a block trade with notional ~6M
        trade = _make_trade("BTC-30MAY25-60000-C", amount=1000, price=0.1)
        cache.add_trade(trade)

        # Filter at 1M should include the trade
        result_low = calculate_block_trades(
            cache, "BTC", Decimal("1000000"),
        )
        assert result_low.total_count == 1

        # Filter at 10M should exclude the trade
        result_high = calculate_block_trades(
            cache, "BTC", Decimal("10000000"),
        )
        assert result_high.total_count == 0
