"""Tests for Polars harmonisation and fiat gravity scoring."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import polars as pl
import pytest

from mcp_servers.predict_macro.background_sync import build_stablecoin_flows_snapshot
from mcp_servers.predict_macro.config import (
    FRED_SERIES_DXY,
    FRED_SERIES_M2,
    FRED_SERIES_SOFR,
    FRED_SERIES_US10Y,
)
from mcp_servers.predict_macro.macro_engine import (
    calculate_series_latest_and_pct_change,
    calculate_tradfi_state,
    observations_dict_to_daily_wide,
    run_harmonization_pipeline,
    stablecoin_events_to_daily_net,
)


def test_calculate_series_latest_and_pct_change_empty() -> None:
    """Empty observations yield None pair."""
    latest, pct = calculate_series_latest_and_pct_change([])
    assert latest is None and pct is None


def test_calculate_series_latest_and_pct_change_monotonic() -> None:
    """Approximate 30d pct uses earliest observation on or before latest-32d."""
    base: date = date(2025, 1, 1)
    obs: list[tuple[date, Decimal]] = [
        (base, Decimal("100")),
        (base + timedelta(days=40), Decimal("110")),
    ]
    latest, pct = calculate_series_latest_and_pct_change(obs)
    assert latest == pytest.approx(110.0)
    assert pct is not None
    assert pct == pytest.approx(10.0)


def test_calculate_tradfi_state_degraded_when_series_missing() -> None:
    """Missing series marks TradFiState as DEGRADED."""
    state = calculate_tradfi_state({}, datetime.now(tz=timezone.utc))
    assert state.status == "DEGRADED"


def test_calculate_tradfi_state_ok() -> None:
    """Populated series yields OK baseline snapshot."""
    d0: date = date(2025, 3, 1)
    observations = {
        FRED_SERIES_DXY: [(d0, Decimal("120")), (d0 + timedelta(days=5), Decimal("118"))],
        FRED_SERIES_US10Y: [(d0, Decimal("4.5")), (d0 + timedelta(days=5), Decimal("4.4"))],
        FRED_SERIES_SOFR: [(d0, Decimal("5.3")), (d0 + timedelta(days=5), Decimal("5.25"))],
        FRED_SERIES_M2: [(d0, Decimal("21000")), (d0 + timedelta(days=5), Decimal("21050"))],
    }
    state = calculate_tradfi_state(observations)
    assert state.status == "OK"
    assert state.dxy_latest == pytest.approx(118.0)


def test_stablecoin_events_to_daily_net_groups() -> None:
    """Events collapse to calendar-day sums."""
    day = date(2025, 4, 1)
    ts1 = datetime(day.year, day.month, day.day, 1, tzinfo=timezone.utc)
    ts2 = datetime(day.year, day.month, day.day, 22, tzinfo=timezone.utc)
    events = [(ts1, Decimal("100")), (ts2, Decimal("-40"))]
    df = stablecoin_events_to_daily_net(events)
    assert df.height == 1
    row = df.row(0, named=True)
    assert float(row["net_total"]) == pytest.approx(60.0)


def test_observations_forward_fill() -> None:
    """Outer-joined TradFi rows carry prior levels forward across sparse dates."""
    d0 = date(2025, 5, 1)
    observations = {
        FRED_SERIES_DXY: [(d0, Decimal("110")), (d0 + timedelta(days=1), Decimal("109"))],
        FRED_SERIES_US10Y: [(d0, Decimal("4.0")), (d0 + timedelta(days=2), Decimal("3.9"))],
        FRED_SERIES_SOFR: [(d0, Decimal("5.0")), (d0 + timedelta(days=2), Decimal("4.95"))],
        FRED_SERIES_M2: [(d0, Decimal("20000")), (d0 + timedelta(days=2), Decimal("20020"))],
    }
    wide = observations_dict_to_daily_wide(observations)
    assert wide.height >= 3
    gap_row = wide.filter(pl.col("date") == d0 + timedelta(days=1)).row(0, named=True)
    assert gap_row["us10y"] == pytest.approx(4.0)
    assert gap_row["sofr"] == pytest.approx(5.0)


def test_run_harmonization_pipeline_short_history_returns_neutral() -> None:
    """Thin overlap yields degraded neutral gravity score."""
    d0 = date(2025, 6, 1)
    observations = {
        FRED_SERIES_DXY: [(d0 + timedelta(days=i), Decimal(str(100 + i))) for i in range(8)],
        FRED_SERIES_US10Y: [(d0 + timedelta(days=i), Decimal("4")) for i in range(8)],
        FRED_SERIES_SOFR: [(d0 + timedelta(days=i), Decimal("5")) for i in range(8)],
        FRED_SERIES_M2: [(d0 + timedelta(days=i), Decimal("20000")) for i in range(8)],
    }
    stamped = [
        (datetime(d0.year, d0.month, d0.day, 12, tzinfo=timezone.utc), Decimal("1e6")),
    ]
    _panel, report = run_harmonization_pipeline(observations, stamped)
    assert report.status == "DEGRADED"
    assert report.regime_signal == "NEUTRAL"


def test_background_stablecoin_snapshot_math() -> None:
    """Rolling-window sums use UTC cutoff semantics."""
    now = datetime(2025, 7, 1, 12, 0, tzinfo=timezone.utc)
    old = now - timedelta(hours=30)
    usdt = [(now, Decimal("500")), (old, Decimal("999"))]
    snap = build_stablecoin_flows_snapshot(usdt, [], now, populated=True)
    assert snap.usdt_net_usd_24h == Decimal("500")
    assert snap.combined_net_usd_24h == Decimal("500")
