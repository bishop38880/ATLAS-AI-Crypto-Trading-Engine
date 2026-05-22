"""Shared async backtest execution for CLI commands."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from decimal import Decimal

from backtesting.analytics.exporter import BacktestExporter
from backtesting.analytics.monte_carlo import MonteCarloConfig
from backtesting.analytics.result import BacktestResult as AnalyticsResult
from backtesting.analytics.result import build_backtest_result
from backtesting.data.db import BacktestDB
from backtesting.engine.config import BacktestConfig
from backtesting.engine.replay import BacktestReplay


def parse_threshold_triplet(raw_value: str | None) -> tuple[int, int, int]:
    """Parse weak,buy,strong threshold CSV."""
    if raw_value is None:
        return 120, 150, 180
    parts = [int(item.strip()) for item in raw_value.split(",") if item.strip()]
    if len(parts) != 3:
        raise ValueError("Thresholds must be three comma-separated integers: weak,buy,strong")
    return parts[0], parts[1], parts[2]


def parse_asset_list(raw_value: str) -> list[str]:
    """Parse comma-separated asset symbols."""
    return [item.strip().upper() for item in raw_value.split(",") if item.strip()]


async def run_backtest_pipeline(
    config: BacktestConfig,
    *,
    monte_carlo: bool = False,
    on_progress: Callable[[int, int, str], None] | None = None,
    save_to_db: bool = True,
) -> AnalyticsResult:
    """Execute replay and assemble analytics."""
    started = time.perf_counter()
    replay = BacktestReplay(db=None if config.use_synthetic else BacktestDB.instance())
    replay_output = await replay.run(config, on_progress=on_progress)
    duration = Decimal(str(round(time.perf_counter() - started, 3)))
    bar_estimate = _estimate_backtest_bars(config)
    mc_config = MonteCarloConfig() if monte_carlo else None
    result = build_backtest_result(
        trades=replay_output.closed_trades,
        config=config,
        backtest_bars=bar_estimate,
        ohlcv=None,
        mc_config=mc_config,
        duration_seconds=duration,
    )
    if save_to_db and not config.use_synthetic:
        await asyncio.to_thread(BacktestExporter().save_to_db, result, BacktestDB.instance())
    return result


def _estimate_backtest_bars(config: BacktestConfig) -> int:
    if config.use_synthetic:
        return 1400
    return 8760
