"""Monte Carlo simulation on backtest trade sequences."""

from __future__ import annotations

import random
from decimal import Decimal

import numpy as np
from pydantic import BaseModel, Field

from backtesting.analytics.metrics import compute_equity_curve, compute_max_drawdown
from backtesting.engine.position import ClosedTrade

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")


class MonteCarloConfig(BaseModel, frozen=True):
    """Configuration for Monte Carlo resampling."""

    n_simulations: int = 1000
    bootstrap_sequence: bool = True
    randomise_timing: bool = True
    seed: int | None = None


class MonteCarloResult(BaseModel, frozen=True):
    """Aggregated Monte Carlo simulation output."""

    n_simulations: int
    median_total_return_pct: Decimal
    p5_total_return_pct: Decimal
    p95_total_return_pct: Decimal
    median_max_drawdown_pct: Decimal
    p95_max_drawdown_pct: Decimal
    probability_of_loss_pct: Decimal
    probability_exceed_10pct_dd_pct: Decimal = Field(
        description="Share of simulations with max drawdown above 10%.",
    )


def run_monte_carlo(
    trades: list[ClosedTrade],
    initial_account: Decimal,
    mc_config: MonteCarloConfig,
) -> MonteCarloResult:
    """Run CPU-bound Monte Carlo simulation (call via asyncio.to_thread)."""
    rng = random.Random(mc_config.seed)
    if not trades:
        return _empty_monte_carlo_result(mc_config.n_simulations)
    total_returns: list[float] = []
    drawdowns: list[float] = []
    for _ in range(mc_config.n_simulations):
        sampled = _sample_trades(trades, mc_config, rng)
        total_return, max_dd = _simulate_path(sampled, initial_account, mc_config, rng)
        total_returns.append(total_return)
        drawdowns.append(max_dd)
    return _aggregate_simulation_results(mc_config.n_simulations, total_returns, drawdowns)


def _sample_trades(
    trades: list[ClosedTrade],
    mc_config: MonteCarloConfig,
    rng: random.Random,
) -> list[ClosedTrade]:
    sequence = list(trades)
    if mc_config.bootstrap_sequence:
        sequence = [rng.choice(trades) for _ in range(len(trades))]
    if mc_config.randomise_timing:
        sequence = [_shift_trade_timing(trade, rng) for trade in sequence]
    return sequence


def _shift_trade_timing(trade: ClosedTrade, rng: random.Random) -> ClosedTrade:
    shift = rng.randint(-3, 3)
    new_entry_index = max(0, trade.entry.entry_bar_index + shift)
    new_exit_index = max(new_entry_index + 1, trade.exit.exit_bar_index + shift)
    entry = trade.entry.model_copy(update={"entry_bar_index": new_entry_index})
    trade_exit = trade.exit.model_copy(
        update={
            "exit_bar_index": new_exit_index,
            "duration_bars": new_exit_index - new_entry_index,
        },
    )
    return trade.model_copy(update={"entry": entry, "exit": trade_exit})


def _simulate_path(
    trades: list[ClosedTrade],
    initial_account: Decimal,
    mc_config: MonteCarloConfig,
    rng: random.Random,
) -> tuple[float, float]:
    del mc_config, rng
    equity_curve = compute_equity_curve(trades, initial_account)
    total_pnl = sum((trade.exit.pnl_usd for trade in trades), start=_ZERO)
    total_return = float(total_pnl / initial_account * _HUNDRED) if initial_account > _ZERO else 0.0
    max_dd_pct, _ = compute_max_drawdown(equity_curve)
    return total_return, float(max_dd_pct)


def _aggregate_simulation_results(
    n_simulations: int,
    total_returns: list[float],
    drawdowns: list[float],
) -> MonteCarloResult:
    returns_array = np.array(total_returns, dtype=np.float64)
    drawdown_array = np.array(drawdowns, dtype=np.float64)
    loss_probability = float(np.mean(returns_array < 0.0) * 100.0)
    exceed_dd_probability = float(np.mean(drawdown_array > 10.0) * 100.0)
    return MonteCarloResult(
        n_simulations=n_simulations,
        median_total_return_pct=Decimal(str(np.median(returns_array))),
        p5_total_return_pct=Decimal(str(np.percentile(returns_array, 5))),
        p95_total_return_pct=Decimal(str(np.percentile(returns_array, 95))),
        median_max_drawdown_pct=Decimal(str(np.median(drawdown_array))),
        p95_max_drawdown_pct=Decimal(str(np.percentile(drawdown_array, 95))),
        probability_of_loss_pct=Decimal(str(loss_probability)),
        probability_exceed_10pct_dd_pct=Decimal(str(exceed_dd_probability)),
    )


def _empty_monte_carlo_result(n_simulations: int) -> MonteCarloResult:
    return MonteCarloResult(
        n_simulations=n_simulations,
        median_total_return_pct=_ZERO,
        p5_total_return_pct=_ZERO,
        p95_total_return_pct=_ZERO,
        median_max_drawdown_pct=_ZERO,
        p95_max_drawdown_pct=_ZERO,
        probability_of_loss_pct=_ZERO,
        probability_exceed_10pct_dd_pct=_ZERO,
    )
