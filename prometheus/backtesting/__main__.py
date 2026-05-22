"""Backtest CLI — agent-based simulation mode.

Usage::

    python -m prometheus.backtesting \\
        --simulator=agent_based \\
        --symbol=BTCUSDT \\
        --seed=42 \\
        --duration=3600

Runs a deterministic agent-based backtest over synthetic or
recorded L3 order book data.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from loguru import logger

from prometheus.backtesting.agent_based_simulator import (
    AgentBasedSimulator,
    NoiseTrader,
    PolarisSignalAgent,
    SimulatedAgent,
)
from prometheus.backtesting.models import OrderBookSnapshot, SimulationResult
from prometheus.backtesting.order_book_replay import OrderBookReplayEngine
from prometheus.backtesting.synthetic_data import generate_book_snapshots


def main() -> None:
    """Entry point for ``python -m prometheus.backtesting``."""
    args = _parse_args()
    if args.simulator != "agent_based":
        logger.error("unsupported_simulator | mode={}", args.simulator)
        sys.exit(1)
    result = asyncio.run(_run_agent_simulation(args))
    _print_report(result)


# ── Argument Parsing ─────────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    """Parse CLI arguments for the backtest runner."""
    parser = argparse.ArgumentParser(
        description="POLARIS Backtesting CLI — Agent-Based Simulation",
    )
    _add_simulation_args(parser)
    return parser.parse_args()


def _add_simulation_args(parser: argparse.ArgumentParser) -> None:
    """Register all CLI arguments on the parser."""
    parser.add_argument(
        "--simulator",
        choices=["agent_based"],
        default="agent_based",
        help="Simulation mode (default: agent_based)",
    )
    parser.add_argument(
        "--symbol", default="BTCUSDT",
        help="Trading pair to simulate (default: BTCUSDT)",
    )
    parser.add_argument(
        "--seed", type=int, default=42,
        help="RNG seed for deterministic replay (default: 42)",
    )
    parser.add_argument(
        "--duration", type=int, default=3600,
        help="Simulation duration in seconds (default: 3600)",
    )
    parser.add_argument(
        "--base-price", type=float, default=50000.0,
        help="Base price for synthetic data (default: 50000)",
    )
    parser.add_argument(
        "--noise-rate", type=float, default=0.1,
        help="Noise trader order rate per tick (default: 0.1)",
    )


# ── Simulation Runner ────────────────────────────────────────────────


async def _run_agent_simulation(
    args: argparse.Namespace,
) -> SimulationResult:
    """Build components and run the agent-based simulation."""
    logger.info(
        "backtest_start | symbol={} | seed={} | duration={}s",
        args.symbol, args.seed, args.duration,
    )
    snapshots = generate_book_snapshots(
        symbol=args.symbol,
        base_price=args.base_price,
        duration_seconds=args.duration,
        seed=args.seed,
    )
    replay = OrderBookReplayEngine.from_snapshots(snapshots)
    agents = _build_agents(args, snapshots)
    simulator = AgentBasedSimulator(
        replay_engine=replay,
        agents=agents,
        seed=args.seed,
    )
    return simulator.run()


def _build_agents(
    args: argparse.Namespace,
    snapshots: list[OrderBookSnapshot],
) -> list[SimulatedAgent]:
    """Construct the agent list for the simulation."""
    sample_signals = _generate_sample_signals(snapshots, args)
    polaris = PolarisSignalAgent(signals=sample_signals)
    noise = NoiseTrader(
        order_rate=args.noise_rate,
        max_size=0.5,
    )
    return [polaris, noise]


def _generate_sample_signals(
    snapshots: list[OrderBookSnapshot],
    args: argparse.Namespace,
) -> list[tuple[datetime, Literal["buy", "sell"], Decimal]]:
    """Create sample POLARIS signals at regular intervals."""
    import numpy as np

    rng = np.random.default_rng(args.seed + 1000)
    interval = max(1, len(snapshots) // 20)
    signals: list[tuple[datetime, Literal["buy", "sell"], Decimal]] = []

    for i in range(0, len(snapshots), interval):
        snap = snapshots[i]
        side: Literal["buy", "sell"] = "buy" if rng.random() > 0.5 else "sell"
        size = Decimal(str(round(rng.uniform(0.01, 0.1), 4)))
        signals.append((snap.timestamp, side, size))

    return signals


# ── Report Printing ──────────────────────────────────────────────────


def _print_report(result: SimulationResult) -> None:
    """Print simulation summary to stdout."""
    logger.info(
        "backtest_complete | symbol={} | trades={} | avg_slip={}bps",
        result.symbol, result.total_trades, result.avg_slippage_bps,
    )
    _print_sim_header(result)
    _print_performance_section(result)
    print("=" * 60 + "\n")


def _print_sim_header(result: SimulationResult) -> None:
    """Print basic simulation metadata."""
    print("\n" + "=" * 60)
    print("  AGENT-BASED SIMULATION REPORT")
    print("=" * 60)
    print("  Symbol:             {}".format(result.symbol))
    print("  Seed:               {}".format(result.seed))
    print("  Total Trades:       {}".format(result.total_trades))
    print("  Avg Slippage (bps): {}".format(result.avg_slippage_bps))
    print("  Max Slippage (bps): {}".format(result.max_slippage_bps))
    print("  Partial Fill Rate:  {}".format(result.partial_fill_rate))
    print("  Wall Clock (s):     {}".format(result.wall_clock_seconds))
    print("  Sim Duration (s):   {}".format(result.simulated_duration_seconds))
    print("  Speedup Factor:     {}x".format(result.speedup_factor))


def _print_performance_section(result: SimulationResult) -> None:
    """Print financial attribution sections."""
    if not result.performance:
        return
        
    p = result.performance
    print("\n  PERFORMANCE ATTRIBUTION")
    print("-" * 30)
    print("  Total P&L:          ${}".format(p.total_pnl.quantize(Decimal("0.01"))))
    print("  PnL %:              {}%".format(p.pnl_pct))
    print("  Max Drawdown:       {}%".format(p.max_drawdown_pct))
    print("  Total Fees:         ${}".format(p.total_fees.quantize(Decimal("0.01"))))
    print("  Total Volume:       ${}".format(p.total_volume.quantize(Decimal("0.01"))))

    if p.agent_stats:
        _print_agent_breakdown(p.agent_stats)
    if p.regime_stats:
        _print_regime_breakdown(p.regime_stats)


def _print_agent_breakdown(agent_stats: dict) -> None:
    """Print per-agent P&L and volume."""
    print("\n  AGENT ATTRIBUTION")
    print("-" * 30)
    for aid, stats in agent_stats.items():
        print("  Agent: {}".format(aid))
        print("    P&L:     ${}".format(stats.total_pnl))
        print("    Volume:  ${}".format(stats.total_volume))
        print("    Trades:  {}".format(stats.trade_count))
        print("    Slip:    {}bps".format(stats.avg_slippage_bps))


def _print_regime_breakdown(regime_stats: dict) -> None:
    """Print per-regime P&L and counts."""
    print("\n  REGIME ATTRIBUTION")
    print("-" * 30)
    for reg, stats in regime_stats.items():
        print("  Regime: {}".format(reg))
        print("    P&L:     ${}".format(stats.total_pnl))
        print("    Trades:  {}".format(stats.trade_count))
        print("    Slip:    {}bps".format(stats.avg_slippage_bps))


if __name__ == "__main__":
    main()
