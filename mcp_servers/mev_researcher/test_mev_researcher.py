"""Unit tests for MEV Researcher cache and MTI engine (no live RPC)."""

from __future__ import annotations

import time
from decimal import Decimal

import pytest

from mcp_servers.mev_researcher.cache_layer import ThreatCacheCoordinator, canonical_evm_address
from mcp_servers.mev_researcher.mev_engine import (
    calculate_mti,
    derive_execution_strategy,
    scan_pending_for_rugs,
)
from mcp_servers.mev_researcher.models import PendingMempoolEvent


def _ev_evt(
    *,
    pool: str = "0xa0b86991c6218b36c1d19d4a2e9eb0cE3606eB48",
    bias: str = "neutral",
    complex_route: bool = False,
    drain: bool = False,
    usd: str = "1000",
) -> PendingMempoolEvent:
    return PendingMempoolEvent(
        chain="ethereum",
        tx_signature="0xabc",
        pool_address=canonical_evm_address(pool),
        detected_at_unix=time.time(),
        directional_bias=bias,  # type: ignore[arg-type]
        estimated_value_usd=Decimal(usd),
        priority_fee_micro_usd_proxy=Decimal("0.05"),
        calldata_preview_hex="0xa9059cbb0000",
        instruction_count_sol=0,
        is_complex_route=complex_route,
        flagged_drain_candidate=drain,
    )


def test_calculate_mti_empty_is_benign() -> None:
    score, threats, vol = calculate_mti([])
    assert score < 0.3
    assert threats == []
    assert vol == Decimal("0")


def test_calculate_mti_high_complexity_raises_mti() -> None:
    rows = [_ev_evt(complex_route=True) for _ in range(60)]
    score, threats, _vol = calculate_mti(rows)
    assert score >= 0.3
    assert any("sandwich" in t or "complex" in t for t in threats)


@pytest.mark.asyncio
async def test_watchlist_round_trip() -> None:
    coord = ThreatCacheCoordinator()
    ok, detail = await coord.manage_watchlist(
        "ethereum",
        "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        "watch",
    )
    assert ok is True
    assert detail == "watch_registered"
    snap = await coord.snapshot_for_pool(
        "ethereum",
        "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
    )
    assert snap == []
    evt = _ev_evt(pool="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48")
    await coord.ingest_event(evt)
    snap2 = await coord.snapshot_for_pool(
        "ethereum",
        "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
    )
    assert len(snap2) == 1


def test_derive_execution_strategy_benign_band() -> None:
    rec = derive_execution_strategy(
        mti_score=0.1,
        event_count=3,
        size_trigger_usd=Decimal("1000"),
        side_literal="LONG",
    )
    assert rec.strategy == "VWAP"


def test_derive_execution_strategy_hostile() -> None:
    rec = derive_execution_strategy(
        mti_score=0.75,
        event_count=20,
        size_trigger_usd=Decimal("5000"),
        side_literal="LONG",
    )
    assert rec.strategy == "PROTECTIVE_SWARM"


def test_scan_pending_for_rugs_flags_cluster() -> None:
    evts = [_ev_evt(drain=True) for _ in range(5)]
    report = scan_pending_for_rugs(
        chain="ethereum",
        pool_address="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        events=evts,
    )
    assert report.drain_detected is True
    assert report.chain == "ethereum"
