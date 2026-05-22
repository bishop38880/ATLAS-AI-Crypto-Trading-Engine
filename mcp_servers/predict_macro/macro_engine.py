"""
Polars-based harmonisation and fiat gravity scoring.

MacroCrossMarketAgent - Fiat Gravity Engine merges TradFi (forward-filled across
weekends) with daily stablecoin mint-burn nets. Uses Polars (not pandas) per
ATLAS time-series policy.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Literal

import polars as pl
from loguru import logger

from .config import (
    FRED_SERIES_DXY,
    FRED_SERIES_M2,
    FRED_SERIES_SOFR,
    FRED_SERIES_US10Y,
)
from .models import LiquidityRegimeReport, TradFiState


def calculate_series_latest_and_pct_change(
    observations: list[tuple[date, Decimal]],
) -> tuple[float | None, float | None]:
    """
    Compute latest observation and approximate 30-calendar-day percentage change.
    """
    if not observations:
        return None, None

    latest_date: date
    latest_val: Decimal
    latest_date, latest_val = observations[-1]

    target_start: date = latest_date - timedelta(days=32)
    baseline_val: Decimal | None = None
    for obs_date, obs_val in observations:
        if obs_date <= target_start:
            baseline_val = obs_val

    latest_float: float = float(latest_val)
    if baseline_val is None or baseline_val == Decimal("0"):
        return latest_float, None

    pct: float = float((latest_val - baseline_val) / baseline_val * Decimal("100"))
    return latest_float, pct


def calculate_tradfi_state(
    observations_by_series: dict[str, list[tuple[date, Decimal]]],
    now_utc: datetime | None = None,
) -> TradFiState:
    """
    Build TradFiState from cached FRED observations.

    MacroCrossMarketAgent - Fiat Gravity Engine reads this as baseline TradFi.
    """
    ts: datetime = now_utc or datetime.now(tz=timezone.utc)

    def _pull(series_id: str) -> tuple[float, float]:
        series_obs: list[tuple[date, Decimal]] = observations_by_series.get(
            series_id, []
        )
        latest_v: float | None
        pct_v: float | None
        latest_v, pct_v = calculate_series_latest_and_pct_change(series_obs)
        if latest_v is None:
            return 0.0, 0.0
        pct_safe: float = float(pct_v) if pct_v is not None else 0.0
        return latest_v, pct_safe

    dxy: tuple[float, float] = _pull(FRED_SERIES_DXY)
    us10y: tuple[float, float] = _pull(FRED_SERIES_US10Y)
    sofr: tuple[float, float] = _pull(FRED_SERIES_SOFR)
    m2: tuple[float, float] = _pull(FRED_SERIES_M2)

    degraded: bool = any(
        len(observations_by_series.get(sid, [])) == 0
        for sid in (
            FRED_SERIES_DXY,
            FRED_SERIES_US10Y,
            FRED_SERIES_SOFR,
            FRED_SERIES_M2,
        )
    )

    return TradFiState(
        dxy_latest=dxy[0],
        dxy_pct_change_30d=dxy[1],
        us10y_latest=us10y[0],
        us10y_pct_change_30d=us10y[1],
        sofr_latest=sofr[0],
        sofr_pct_change_30d=sofr[1],
        m2_latest=m2[0],
        m2_pct_change_30d=m2[1],
        observation_timestamp_utc=ts,
        status="DEGRADED" if degraded else "OK",
    )


def stablecoin_events_to_daily_net(
    stamped_events: list[tuple[datetime, Decimal]],
) -> pl.DataFrame:
    """Aggregate signed flows into UTC calendar-day nets."""
    if not stamped_events:
        return pl.DataFrame(
            schema={"date": pl.Date, "net_total": pl.Float64},
        )

    days: list[date] = [evt[0].date() for evt in stamped_events]
    amounts: list[float] = [float(evt[1]) for evt in stamped_events]
    raw: pl.DataFrame = pl.DataFrame({"date": days, "flow_usd": amounts})
    return raw.group_by("date").agg(pl.col("flow_usd").sum().alias("net_total"))


def observations_dict_to_daily_wide(
    observations_by_series: dict[str, list[tuple[date, Decimal]]],
) -> pl.DataFrame:
    """Merge FRED series onto one daily wide frame (outer join on calendar date)."""
    merged: pl.DataFrame | None = None
    rename_map: dict[str, str] = {
        FRED_SERIES_DXY: "dxy",
        FRED_SERIES_US10Y: "us10y",
        FRED_SERIES_SOFR: "sofr",
        FRED_SERIES_M2: "m2",
    }

    for series_id, column_name in rename_map.items():
        rows: list[tuple[date, Decimal]] = observations_by_series.get(series_id, [])
        if not rows:
            continue
        frame: pl.DataFrame = pl.DataFrame(
            {
                "date": [r[0] for r in rows],
                column_name: [float(r[1]) for r in rows],
            }
        )
        if merged is None:
            merged = frame
        else:
            merged = merged.join(frame, on="date", how="full", coalesce=True)

    if merged is None:
        return pl.DataFrame(
            schema={
                "date": pl.Date,
                "dxy": pl.Float64,
                "us10y": pl.Float64,
                "sofr": pl.Float64,
                "m2": pl.Float64,
            },
        )

    merged_sorted: pl.DataFrame = merged.sort("date")
    with_cols: pl.DataFrame = merged_sorted
    for col_name in ("dxy", "us10y", "sofr", "m2"):
        if col_name not in with_cols.columns:
            with_cols = with_cols.with_columns(pl.lit(None).cast(pl.Float64).alias(col_name))

    filled: pl.DataFrame = with_cols.with_columns(
        [
            pl.col("dxy").forward_fill(),
            pl.col("us10y").forward_fill(),
            pl.col("sofr").forward_fill(),
            pl.col("m2").forward_fill(),
        ]
    )
    return filled


def build_harmonized_daily_panel(
    observations_by_series: dict[str, list[tuple[date, Decimal]]],
    stablecoin_daily: pl.DataFrame,
) -> pl.DataFrame:
    """
    Outer-join TradFi with stablecoin mint nets on calendar date, forward-fill TradFi.

    MacroCrossMarketAgent - Fiat Gravity Engine relies on this alignment so weekend
    mints face Friday-forward-filled yields (no spurious gaps).
    """
    tradfi_daily: pl.DataFrame = observations_dict_to_daily_wide(
        observations_by_series
    )
    if tradfi_daily.height == 0 or stablecoin_daily.height == 0:
        return pl.DataFrame()

    joined: pl.DataFrame = tradfi_daily.join(
        stablecoin_daily,
        on="date",
        how="full",
        coalesce=True,
    ).sort("date")

    harmonized: pl.DataFrame = joined.with_columns(
        [
            pl.col("dxy").forward_fill(),
            pl.col("us10y").forward_fill(),
            pl.col("sofr").forward_fill(),
            pl.col("m2").forward_fill(),
            pl.col("net_total").fill_null(0.0),
        ]
    )
    return harmonized.drop_nulls(subset=["dxy", "us10y"])


def _zscore_expr(column: str, window: int) -> pl.Expr:
    """Rolling z-score with epsilon guard on standard deviation."""
    mean_expr: pl.Expr = pl.col(column).rolling_mean(window_size=window)
    std_expr: pl.Expr = pl.col(column).rolling_std(window_size=window)
    safe_std: pl.Expr = pl.when(std_expr.abs() < 1e-9).then(None).otherwise(std_expr)
    return (pl.col(column) - mean_expr) / safe_std


def _clip_z_band(value: float) -> float:
    """Clamp z-scores to [-3, 3] for deterministic scoring."""
    if value > 3.0:
        return 3.0
    if value < -3.0:
        return -3.0
    return value


def _build_enriched_harmonized_frame(
    harmonized_daily: pl.DataFrame,
    window: int,
) -> pl.DataFrame:
    """Attach rolling z-scores and TradFi liquidity impulse column."""
    return harmonized_daily.with_columns(
        [
            _zscore_expr("us10y", window).alias("z_us10y"),
            _zscore_expr("dxy", window).alias("z_dxy"),
            _zscore_expr("m2", window).alias("z_m2"),
            _zscore_expr("net_total", window).alias("z_net_stable"),
        ]
    ).with_columns(
        (
            (-pl.col("z_us10y").fill_null(0.0) - pl.col("z_dxy").fill_null(0.0)) / 2.0
        ).alias("tradfi_liquidity_impulse"),
    )


def _pearson_impulse_vs_stable_mints(enriched: pl.DataFrame) -> float | None:
    """Pearson correlation: liquidity impulse vs daily stablecoin net."""
    try:
        corr_df: pl.DataFrame = enriched.select(
            pl.corr(
                "tradfi_liquidity_impulse",
                "net_total",
                method="pearson",
            ).alias("c")
        )
        raw_corr: Any = corr_df["c"][0]
        if raw_corr is None:
            return None
        if isinstance(raw_corr, float) and math.isnan(raw_corr):
            return None
        if isinstance(raw_corr, (float, int)):
            return float(raw_corr)
        return None
    except Exception as exc:
        logger.warning("Correlation compute failed | error={}", exc)
        return None


def _fiat_gravity_score_and_regime(
    impulse: float,
    z_m2: float,
    z_stable: float,
) -> tuple[float, Literal["EXPANSION", "CONTRACTION", "NEUTRAL"]]:
    """Map macro z-scores to [0,100] score and discrete regime."""
    clipped_impulse: float = _clip_z_band(impulse)
    clipped_m2: float = _clip_z_band(z_m2)
    clipped_stable: float = _clip_z_band(z_stable)
    combined: float = (
        0.30 * clipped_impulse + 0.25 * clipped_m2 + 0.45 * clipped_stable
    )
    score_raw: float = 50.0 + 12.0 * combined
    score: float = max(0.0, min(100.0, score_raw))
    regime: Literal["EXPANSION", "CONTRACTION", "NEUTRAL"]
    if score >= 62.0:
        regime = "EXPANSION"
    elif score <= 38.0:
        regime = "CONTRACTION"
    else:
        regime = "NEUTRAL"
    return score, regime


def calculate_liquidity_regime_report(
    harmonized_daily: pl.DataFrame,
) -> LiquidityRegimeReport:
    """
    Deterministic fiat gravity score, regime label, and 30d correlation.

    MacroCrossMarketAgent - Fiat Gravity Engine: score≈95 implies coordinated
    yield compression and aggressive stablecoin minting — maximum risk-on tilt.
    """
    if harmonized_daily.height < 10:
        return LiquidityRegimeReport(
            fiat_gravity_score=50.0,
            regime_signal="NEUTRAL",
            correlation_tradfi_yield_vs_stablecoin_mints_30d=0.0,
            reasoning=(
                "Insufficient aligned history for MacroCrossMarketAgent - Fiat Gravity Engine "
                "(need more dual-layer observations)."
            ),
            status="DEGRADED",
        )

    window: int = min(30, harmonized_daily.height)
    enriched: pl.DataFrame = _build_enriched_harmonized_frame(
        harmonized_daily,
        window,
    )
    corr_optional: float | None = _pearson_impulse_vs_stable_mints(enriched)
    corr_out: float = corr_optional if corr_optional is not None else 0.0

    last_row: dict[str, Any] = enriched.tail(1).to_dicts()[0]
    z_us10y: float = float(last_row.get("z_us10y") or 0.0)
    z_dxy: float = float(last_row.get("z_dxy") or 0.0)
    z_m2: float = float(last_row.get("z_m2") or 0.0)
    z_stable: float = float(last_row.get("z_net_stable") or 0.0)
    impulse: float = (-z_us10y - z_dxy) / 2.0

    score: float
    regime: Literal["EXPANSION", "CONTRACTION", "NEUTRAL"]
    score, regime = _fiat_gravity_score_and_regime(impulse, z_m2, z_stable)

    reasoning: str = (
        "MacroCrossMarketAgent - Fiat Gravity Engine synthesis | "
        "tradfi_impulse_z={:.2f} | m2_z={:.2f} | stable_net_z={:.2f} | "
        "pearson_yields_vs_mints={:.3f}".format(
            impulse,
            z_m2,
            z_stable,
            corr_out,
        )
    )
    report_status: Literal["OK", "DEGRADED"] = (
        "DEGRADED" if corr_optional is None else "OK"
    )

    return LiquidityRegimeReport(
        fiat_gravity_score=round(score, 2),
        regime_signal=regime,
        correlation_tradfi_yield_vs_stablecoin_mints_30d=round(corr_out, 6),
        reasoning=reasoning,
        status=report_status,
    )


def run_harmonization_pipeline(
    observations_by_series: dict[str, list[tuple[date, Decimal]]],
    stamped_stablecoin_events: list[tuple[datetime, Decimal]],
) -> tuple[pl.DataFrame, LiquidityRegimeReport]:
    """Thread/offload entrypoint: daily stablecoin series + harmonised panel + report."""
    stable_daily: pl.DataFrame = stablecoin_events_to_daily_net(stamped_stablecoin_events)
    panel: pl.DataFrame = build_harmonized_daily_panel(
        observations_by_series,
        stable_daily,
    )
    report: LiquidityRegimeReport = calculate_liquidity_regime_report(panel)
    return panel, report
