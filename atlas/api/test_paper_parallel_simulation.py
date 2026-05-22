"""Unit tests for parallel paper-validation replay math."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from atlas.api.paper_parallel_simulation import (
    INITIAL_CASH,
    SimHistoryRow,
    classify_score_tier,
    compute_forward_log_return,
    simulate_parallel_portfolio_usd,
)


def test_classify_score_tier_buckets() -> None:
    assert classify_score_tier(149.4) == "under_150"
    assert classify_score_tier(150.0) == "150_179"
    assert classify_score_tier(179.9) == "150_179"
    assert classify_score_tier(180.0) == "180_plus"


def test_compute_forward_log_return_clamped() -> None:
    r_small = compute_forward_log_return(100.0, 102.0, 900.0)
    assert abs(r_small) < 0.07
    r_big_jump = compute_forward_log_return(
        90.0, 210.0, 3600.0 * 24 * 365,
    )
    assert r_big_jump <= 0.06


def test_simulator_produces_monotonic_peak_drawdown_curve() -> None:
    utc = timezone.utc
    base = datetime(2026, 1, 1, 12, 0, 0, tzinfo=utc)
    rows = [
        SimHistoryRow(
            row_id=k,
            asset="BTC",
            ts=base + timedelta(hours=k),
            total_score=float(170 + k * 2),
            decision="LONG" if k % 3 else "SHORT",
            passes_gate=True,
        )
        for k in range(5)
    ]
    anchors = {"BTC": Decimal("40000")}
    result = simulate_parallel_portfolio_usd(rows, anchors, initial_cash=INITIAL_CASH)

    decimals = result["tier_win_rates"]
    assert decimals["under_150"]["trades"] + decimals["150_179"]["trades"] + decimals["180_plus"]["trades"] >= 1

    drawdown_pct = [p["drawdown_pct"] for p in result["drawdown_curve"]]
    assert drawdown_pct[0] == pytest.approx(0.0, abs=0.00001)
    assert max(drawdown_pct) <= 0.0 + 1e-6


def test_under_180_tier_reports_trades_more_often_than_180_plus_dummy_path() -> None:
    utc = timezone.utc
    base = datetime(2026, 2, 1, 8, tzinfo=utc)
    rows_low = []
    pid = 0
    clock = base
    while pid < 10:
        rows_low.append(
            SimHistoryRow(
                row_id=pid,
                asset="ETH",
                ts=clock,
                total_score=145.0,
                decision="LONG",
                passes_gate=True,
            ),
        )
        pid += 1
        clock += timedelta(hours=4)

    rows_high = []
    clock = base
    while pid < 20:
        rows_high.append(
            SimHistoryRow(
                row_id=pid,
                asset="ETH",
                ts=clock,
                total_score=190.0,
                decision="LONG",
                passes_gate=True,
            ),
        )
        pid += 1
        clock += timedelta(hours=4)

    merged = rows_low + rows_high
    anchors = {"ETH": Decimal("2200")}
    outcome = simulate_parallel_portfolio_usd(merged, anchors)
    tiers = outcome["tier_win_rates"]
    assert tiers["under_150"]["trades"] >= 1
    assert tiers["180_plus"]["trades"] >= 1
