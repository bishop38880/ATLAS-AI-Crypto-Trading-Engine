"""Tests for autonomous RAG analysis runner helpers."""

from __future__ import annotations

from atlas.core.autonomous_rag_analysis import (
    calculate_analysis_asset_configs,
    default_agent_market_data,
    prioritized_analysis_symbols,
)


def test_default_agent_market_data_non_empty_and_ohlc_lengths_align() -> None:
    """Technical + regime paths require non-empty dict and aligned series."""
    data = default_agent_market_data("BTC/USDT")

    assert data
    assert len(data["close"]) == len(data["volume"]) == 100
    assert "rsi_14" in data
    assert "zscore" in data
    assert "oi_change_4h" in data
    assert "basis_annualised" in data
    assert "vol_zscore" in data
    assert "polarity_percentile" in data


def test_default_agent_market_data_varies_by_asset() -> None:
    """Fallback data should not force identical scores across all assets."""
    btc = default_agent_market_data("BTC/USDT")
    eth = default_agent_market_data("ETH/USDT")

    assert btc["close"] != eth["close"]
    assert btc["rsi_14"] != eth["rsi_14"]


def test_calculate_analysis_asset_configs_includes_dashboard_fallback() -> None:
    """The autonomous loop should score the same fallback ladder the dashboard renders."""
    assets = calculate_analysis_asset_configs([])
    symbols = [asset.symbol for asset in assets]

    assert "BTC/USDT" in symbols
    assert "IMX/USDT" in symbols
    assert len(symbols) >= 33


def test_calculate_analysis_asset_configs_normalises_rotation_symbols() -> None:
    """Redis rotation members arrive in mixed symbol formats."""
    assets = calculate_analysis_asset_configs(["btcusdt", "ETH/USDT", "sol-usdt", "NO_PERP/USDT"])
    symbols = [asset.symbol for asset in assets]

    assert symbols[:3] == ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
    assert "NO_PERP/USDT" not in symbols


def test_prioritized_analysis_scores_dashboard_ladder_before_bulk_universe() -> None:
    """Wide universe lists must not push dashboard ladder rows behind dozens of alts."""
    universe_bulk = ["ZZZ/USDT", "AAA/USDT", "BTC/USDT"]
    active_ladder = ["BTCUSDT"]
    ordered = prioritized_analysis_symbols(universe_bulk, active_ladder, [], [])

    assert ordered[0] == "BTC/USDT"
    assert ordered.index("BTC/USDT") < ordered.index("ZZZ/USDT")


def test_prioritized_analysis_inserts_operator_pins_before_daily_and_universe_tail() -> None:
    """Pinned dashboard symbols must score before bulk universe processing."""
    ordered = prioritized_analysis_symbols(
        universe_members=["YYY/USDT"],
        active_33_members=["BTCUSDT"],
        daily_8_members=["DDD/USDT"],
        dashboard_pin_members=["ZZZ/USDT"],
    )
    assert ordered.index("BTC/USDT") < ordered.index("ZZZ/USDT")
    assert ordered.index("ZZZ/USDT") < ordered.index("DDD/USDT")
    assert ordered.index("DDD/USDT") < ordered.index("YYY/USDT")
