"""Smoke tests for ``backend.config.asset_universe`` — canonical PROMETHEUS / POLARIS ladder."""

from backend.config.asset_universe import (
    POLARIS_DASHBOARD_ASSETS,
    POLARIS_DASHBOARD_CARD_COUNT,
    POLARIS_DASHBOARD_FALLBACK_BASES_ORDER,
    POLARIS_DASHBOARD_TIER_ONE_ORDER,
    PolarisDashboardTier,
)


def test_dashboard_row_count_contract() -> None:
    """Thirty-three cards; eighteen tier-one ALWAYS_ON majors."""
    assert POLARIS_DASHBOARD_CARD_COUNT == 33
    assert len(POLARIS_DASHBOARD_ASSETS) == 33
    assert POLARIS_DASHBOARD_FALLBACK_BASES_ORDER[0] == "BTC"


def test_tier_one_row_count_contract() -> None:
    tiers = [row.tier for row in POLARIS_DASHBOARD_ASSETS]
    assert tiers.count(PolarisDashboardTier.ALWAYS_ON) == 18
    assert tiers.count(PolarisDashboardTier.ROTATION) == 15
    assert len(POLARIS_DASHBOARD_TIER_ONE_ORDER) == 18


def test_bitget_symbols_contract() -> None:
    """Bitget perpetual keys follow uppercase ``BASEUSDT``."""
    btc = POLARIS_DASHBOARD_ASSETS[0]
    assert btc.symbol_bitget_usdt == "BTCUSDT"
    eth = POLARIS_DASHBOARD_ASSETS[1]
    assert eth.symbol_bitget_usdt == "ETHUSDT"
