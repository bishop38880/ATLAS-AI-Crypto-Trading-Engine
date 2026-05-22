"""Tests for hydra_base_asset normalisation."""

from atlas.shared.hydra_asset import hydra_base_asset


def test_hydra_base_asset_slash_pair() -> None:
    assert hydra_base_asset("BTC/USDT") == "BTC"


def test_hydra_base_asset_concat_usdt() -> None:
    assert hydra_base_asset("BTCUSDT") == "BTC"


def test_hydra_base_asset_perp_suffix() -> None:
    assert hydra_base_asset("BTC-PERP") == "BTC"


def test_hydra_base_asset_plain_base() -> None:
    assert hydra_base_asset("ZEC") == "ZEC"
