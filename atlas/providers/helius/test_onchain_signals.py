"""Tests for Helius → confluence OnChainSignals mapping."""

from __future__ import annotations

from decimal import Decimal

from atlas.providers.helius.models import FlowSignals
from atlas.providers.helius.onchain_signals import (
    calculate_whale_outflow_zscore,
    flow_signals_to_onchain,
    helius_flow_is_actionable,
    normalize_solana_base,
)


def test_normalize_solana_base_strips_pair() -> None:
    assert normalize_solana_base("sol/usdt") == "SOL"


def test_flow_signals_to_onchain_maps_helius_source() -> None:
    flow = FlowSignals(
        symbol="SOL",
        exchange_netflow_1h=-50_000.0,
        exchange_netflow_4h=-2_400_000.0,
        exchange_netflow_24h=-2_400_000.0,
        whale_tx_count_1h=2,
        whale_tx_count_4h=8,
        whale_tx_count_24h=14,
        flow_direction="outflow",
        largest_single_tx_24h=900_000.0,
        last_updated="2026-05-20T00:00:00+00:00",
    )
    onchain = flow_signals_to_onchain(flow)
    assert onchain.data_source == "helius"
    assert onchain.exchange_netflow_4h == Decimal("-2400000.0")
    assert calculate_whale_outflow_zscore(-2_400_000.0) > 1.0
    assert helius_flow_is_actionable(onchain) is True


def test_helius_flow_not_actionable_when_all_zeros() -> None:
    flow = FlowSignals(
        symbol="JUP",
        exchange_netflow_1h=0.0,
        exchange_netflow_4h=0.0,
        exchange_netflow_24h=0.0,
        whale_tx_count_1h=0,
        whale_tx_count_4h=0,
        whale_tx_count_24h=0,
        flow_direction="neutral",
        largest_single_tx_24h=0.0,
        last_updated="2026-05-20T00:00:00+00:00",
    )
    onchain = flow_signals_to_onchain(flow)
    assert helius_flow_is_actionable(onchain) is False
