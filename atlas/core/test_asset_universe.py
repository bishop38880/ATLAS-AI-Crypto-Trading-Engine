"""Tests for Asset Universe configuration."""

from backend.config.asset_universe import (
    POLARIS_DASHBOARD_ASSETS,
)

from atlas.core.asset_universe import ASSET_UNIVERSE, get_active_assets, polaris_universe_redis_members


def test_extended_universe_prefix_matches_dashboard_canon() -> None:
    """Polaris ladder must stay bitwise aligned between ``backend.config`` and atlas universe."""
    assert len(POLARIS_DASHBOARD_ASSETS) == 33
    assert len(ASSET_UNIVERSE) >= 33
    for index, dash in enumerate(POLARIS_DASHBOARD_ASSETS):
        cfg = ASSET_UNIVERSE[index]
        configured_base = cfg.symbol.split("/", maxsplit=1)[0].upper()
        assert configured_base == dash.polaris_base
        assert dash.tier.value == cfg.tier.value


def test_asset_universe_has_148_assets() -> None:
    """Top-144 crypto perpetuals plus four ISO precious-metal USDT bases."""
    assert len(ASSET_UNIVERSE) == 148


def test_polaris_universe_redis_members_matches_config() -> None:
    """Rotation API / Redis use compact symbols (BTCUSDT); must stay aligned with universe."""
    compact = polaris_universe_redis_members()
    assert len(compact) == 148
    assert compact[0] == "BTCUSDT"
    assert "/" not in "".join(compact)


def test_active_assets_filtering() -> None:
    """Verify that get_active_assets keeps only perpetual-eligible rows."""
    active = get_active_assets()
    assert len(active) == len(ASSET_UNIVERSE)
    for asset in active:
        assert asset.has_perp is True

def test_asset_config_immutable() -> None:
    """AssetConfig should be frozen."""
    asset = ASSET_UNIVERSE[0]
    import pytest
    with pytest.raises(Exception):
        asset.group = "new_group"  # type: ignore
