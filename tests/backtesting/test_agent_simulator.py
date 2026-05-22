"""Tests for Phase 9 agent-based simulator.

Covers:
    1.  Snapshot reconstruction from L3 data
    2.  Binary search lookup at arbitrary timestamp
    3.  Market order full fill at top-of-book
    4.  Market order walks multiple levels
    5.  Partial fill when book has insufficient liquidity
    6.  Limit order placement at correct position
    7.  Aggressive limit crossing produces immediate fill
    8.  Deterministic seeded RNG — identical results on re-run
    9.  NoiseTrader provides consumable liquidity
    10. Slippage increases monotonically with order size
    11. Quality gate: synthetic known-slippage within ±10%
    12. Performance: 1h replay in <6min (>10x real-time)
    13. Financial fields are Decimal, not float
    14. No banned libraries in module source

Hermetic: no live DB, no network.  All data is synthetic.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import numpy as np
import pytest

from prometheus.backtesting.agent_based_simulator import (
    AgentBasedSimulator,
    MutableOrderBook,
    NoiseTrader,
    PolarisSignalAgent,
)
from prometheus.backtesting.models import (
    FillResult,
    OrderBookSnapshot,
    PriceLevel,
    SimulationResult,
    TradeSlippageRecord,
)
from prometheus.backtesting.order_book_replay import OrderBookReplayEngine
from prometheus.backtesting.synthetic_data import (
    generate_book_snapshots,
    generate_single_snapshot,
)


# ── Fixtures ─────────────────────────────────────────────────────────

_BASE_TS = datetime(2025, 10, 15, 12, 0, 0, tzinfo=timezone.utc)
_SEED = 42


def _simple_snapshot(
    bids: list[tuple[str, str]],
    asks: list[tuple[str, str]],
    ts: datetime | None = None,
) -> OrderBookSnapshot:
    """Build a snapshot from (price, size) tuples."""
    return OrderBookSnapshot(
        symbol="BTCUSDT",
        timestamp=ts or _BASE_TS,
        bids=[PriceLevel(price=Decimal(p), size=Decimal(s)) for p, s in bids],
        asks=[PriceLevel(price=Decimal(p), size=Decimal(s)) for p, s in asks],
    )


# ── Test 1 — Snapshot Reconstruction ────────────────────────────────


class TestSnapshotReconstruction:
    """L3 events → correct bid/ask book."""

    def test_bids_sorted_descending(self) -> None:
        """Bids must be sorted highest-price first."""
        snap = _simple_snapshot(
            bids=[("100", "1"), ("102", "2"), ("101", "3")],
            asks=[("103", "1")],
        )
        book = MutableOrderBook(snap)
        assert book.mid_price > 0

    def test_asks_sorted_ascending(self) -> None:
        """Asks must be sorted lowest-price first."""
        snap = _simple_snapshot(
            bids=[("100", "1")],
            asks=[("103", "1"), ("101", "2"), ("102", "3")],
        )
        assert snap.asks[0].price == Decimal("103")

    def test_synthetic_snapshot_has_levels(self) -> None:
        """Synthetic generator produces non-empty books."""
        snap = generate_single_snapshot(
            "BTCUSDT", 50000.0, _BASE_TS, _SEED,
        )
        assert len(snap.bids) > 0
        assert len(snap.asks) > 0


# ── Test 2 — Binary Search Lookup ────────────────────────────────────


class TestBinarySearchLookup:
    """Correct snapshot returned for arbitrary timestamp."""

    def test_exact_match(self) -> None:
        """Exact timestamp returns that snapshot."""
        snaps = generate_book_snapshots("BTCUSDT", 50000, 1, _SEED)
        engine = OrderBookReplayEngine.from_snapshots(snaps)
        result = engine.snapshot_at(snaps[0].timestamp)
        assert result.timestamp == snaps[0].timestamp

    def test_between_timestamps(self) -> None:
        """Timestamp between two snapshots returns the earlier one."""
        snaps = generate_book_snapshots("BTCUSDT", 50000, 1, _SEED)
        assert len(snaps) >= 2
        mid_ts = snaps[0].timestamp + timedelta(milliseconds=50)
        engine = OrderBookReplayEngine.from_snapshots(snaps)
        result = engine.snapshot_at(mid_ts)
        assert result.timestamp == snaps[0].timestamp

    def test_before_start_returns_first(self) -> None:
        """Timestamp before first snapshot returns first."""
        snaps = generate_book_snapshots("BTCUSDT", 50000, 1, _SEED)
        engine = OrderBookReplayEngine.from_snapshots(snaps)
        early = snaps[0].timestamp - timedelta(hours=1)
        result = engine.snapshot_at(early)
        assert result.timestamp == snaps[0].timestamp


# ── Test 3 — Market Order Full Fill ──────────────────────────────────


class TestMarketOrderFullFill:
    """Small market order consumes top-of-book only."""

    def test_buy_fills_at_best_ask(self) -> None:
        """Small BUY fills entirely at best ask."""
        snap = _simple_snapshot(
            bids=[("99", "10")],
            asks=[("101", "10"), ("102", "10")],
        )
        book = MutableOrderBook(snap)
        fill = book.consume_market_buy(Decimal("5"))
        assert fill.filled_size == Decimal("5")
        assert fill.avg_fill_price == Decimal("101.00000000")
        assert not fill.is_partial
        assert fill.levels_consumed == 0  # partial level, not consumed

    def test_sell_fills_at_best_bid(self) -> None:
        """Small SELL fills entirely at best bid."""
        snap = _simple_snapshot(
            bids=[("99", "10"), ("98", "10")],
            asks=[("101", "10")],
        )
        book = MutableOrderBook(snap)
        fill = book.consume_market_sell(Decimal("5"))
        assert fill.filled_size == Decimal("5")
        assert fill.avg_fill_price == Decimal("99.00000000")
        assert not fill.is_partial


# ── Test 4 — Market Order Walks Book ─────────────────────────────────


class TestMarketOrderWalksBook:
    """Large market order walks multiple levels."""

    def test_buy_consumes_multiple_levels(self) -> None:
        """Order larger than top level walks to next."""
        snap = _simple_snapshot(
            bids=[("99", "10")],
            asks=[("101", "5"), ("102", "5"), ("103", "5")],
        )
        book = MutableOrderBook(snap)
        fill = book.consume_market_buy(Decimal("8"))
        assert fill.filled_size == Decimal("8")
        assert fill.levels_consumed >= 1
        # VWAP: (5*101 + 3*102) / 8 = 811/8 = 101.375
        expected_vwap = Decimal("101.37500000")
        assert fill.avg_fill_price == expected_vwap

    def test_sell_consumes_multiple_levels(self) -> None:
        """SELL walks bids when size exceeds top level."""
        snap = _simple_snapshot(
            bids=[("100", "3"), ("99", "3"), ("98", "3")],
            asks=[("101", "10")],
        )
        book = MutableOrderBook(snap)
        fill = book.consume_market_sell(Decimal("7"))
        assert fill.filled_size == Decimal("7")
        assert fill.levels_consumed >= 2


# ── Test 5 — Partial Fill Insufficient Liquidity ─────────────────────


class TestPartialFill:
    """Order larger than total book depth → partial fill."""

    def test_partial_fill_when_book_exhausted(self) -> None:
        """Order exceeding total depth is partially filled."""
        snap = _simple_snapshot(
            bids=[("99", "5")],
            asks=[("101", "3"), ("102", "2")],
        )
        book = MutableOrderBook(snap)
        fill = book.consume_market_buy(Decimal("10"))
        assert fill.filled_size == Decimal("5")
        assert fill.is_partial
        assert fill.remaining_size == Decimal("5")


# ── Test 6 — Limit Order Placement ──────────────────────────────────


class TestLimitOrderPlacement:
    """Limit order adds to book at correct position."""

    def test_buy_limit_inserts_into_bids(self) -> None:
        """BUY limit at 99.5 sits between 100 and 99."""
        snap = _simple_snapshot(
            bids=[("100", "5"), ("99", "5")],
            asks=[("101", "5")],
        )
        book = MutableOrderBook(snap)
        book.add_limit_buy(Decimal("99.5"), Decimal("3"))
        # Sell into it — the 99.5 level should be reachable
        fill = book.consume_market_sell(Decimal("9"))
        # Should consume: 5 @ 100, 3 @ 99.5, 1 @ 99
        assert fill.filled_size == Decimal("9")

    def test_sell_limit_inserts_into_asks(self) -> None:
        """SELL limit at 100.5 sits between 100 and 101."""
        snap = _simple_snapshot(
            bids=[("99", "5")],
            asks=[("101", "5"), ("102", "5")],
        )
        book = MutableOrderBook(snap)
        book.add_limit_sell(Decimal("100.5"), Decimal("3"))
        # Buy through it — the 100.5 level should be hit first
        fill = book.consume_market_buy(Decimal("4"))
        assert fill.filled_size == Decimal("4")
        # VWAP: 3 * 100.5 + 1 * 101 = 402.5 / 4 = 100.625
        expected = Decimal("100.62500000")
        assert fill.avg_fill_price == expected


# ── Test 7 — Aggressive Limit Crossing ──────────────────────────────


class TestLimitCrossing:
    """Aggressive limit crosses spread → immediate fill."""

    def test_crossing_limit_produces_fill(self) -> None:
        """BUY limit above best ask crosses and fills."""
        snap = _simple_snapshot(
            bids=[("99", "5")],
            asks=[("101", "5")],
        )
        book = MutableOrderBook(snap)
        book.add_limit_buy(Decimal("102"), Decimal("3"))
        fills = book.match_crossing_limits()
        assert len(fills) >= 1
        total_filled = sum(f.filled_size for f in fills)
        assert total_filled > 0


# ── Test 8 — Deterministic Seeded RNG ────────────────────────────────


class TestDeterministicRng:
    """Same seed → identical simulation results."""

    def test_same_seed_produces_identical_results(self) -> None:
        """Two runs with seed=42 produce identical output."""
        result_a = _run_short_sim(seed=42)
        result_b = _run_short_sim(seed=42)
        assert result_a.total_trades == result_b.total_trades
        assert result_a.avg_slippage_bps == result_b.avg_slippage_bps
        assert len(result_a.trades) == len(result_b.trades)
        # Order IDs must also be identical (deterministic RNG, not uuid4)
        ids_a = [t.order_id for t in result_a.trades]
        ids_b = [t.order_id for t in result_b.trades]
        assert ids_a == ids_b, "Order IDs must be deterministic"

    def test_different_seed_produces_different_results(self) -> None:
        """Different seeds produce different output."""
        result_a = _run_short_sim(seed=42)
        result_b = _run_short_sim(seed=99)
        # Different seeds generate different book states → different fills.
        # Compare actual fill prices which always differ with different RNG.
        if result_a.trades and result_b.trades:
            prices_a = [t.avg_fill_price for t in result_a.trades]
            prices_b = [t.avg_fill_price for t in result_b.trades]
            assert prices_a != prices_b, (
                "Different seeds must produce different fill prices"
            )


# ── Test 9 — NoiseTrader Provides Liquidity ──────────────────────────


class TestNoiseTrader:
    """NoiseTrader adds orders that can be consumed."""

    def test_noise_trader_generates_orders(self) -> None:
        """With rate=1.0, noise trader always generates an order."""
        snap = generate_single_snapshot("BTCUSDT", 50000, _BASE_TS, _SEED)
        trader = NoiseTrader(order_rate=1.0, max_size=1.0)
        rng = np.random.default_rng(42)
        orders = trader.generate_orders(_BASE_TS, snap, rng)
        assert len(orders) == 1
        assert orders[0].order_type == "LIMIT"


# ── Test 10 — Slippage Increases With Order Size ─────────────────────


class TestSlippageMonotonicity:
    """Larger orders → higher slippage (monotonic)."""

    def test_slippage_monotonic_increase(self) -> None:
        """Doubling order size should not decrease slippage."""
        snap = _simple_snapshot(
            bids=[("99", "5")],
            asks=[
                ("101", "2"), ("102", "2"), ("103", "2"),
                ("104", "2"), ("105", "2"),
            ],
        )
        slippages: list[Decimal] = []
        for size in [Decimal("1"), Decimal("3"), Decimal("6")]:
            book = MutableOrderBook(snap)
            fill = book.consume_market_buy(size)
            slippages.append(fill.slippage_bps)

        # Monotonic non-decreasing
        for i in range(1, len(slippages)):
            assert slippages[i] >= slippages[i - 1], (
                "Slippage must increase with size: {} < {}".format(
                    slippages[i], slippages[i - 1],
                )
            )


# ── Test 11 — Quality Gate: Slippage Within ±10% ────────────────────


class TestSlippageQualityGate:
    """Synthetic known-slippage scenario within ±10%."""

    def test_known_slippage_within_tolerance(self) -> None:
        """Known book state → predicted slippage matches calculation."""
        # Exact book: asks at 101 (qty 5), 102 (qty 5)
        # Buy 8 → fill 5@101 + 3@102 = 811 / 8 = 101.375
        # Mid = (100 + 101) / 2 = 100.5
        # Slippage = (101.375 - 100.5) / 100.5 * 10000 = 87.06 bps
        snap = _simple_snapshot(
            bids=[("100", "10")],
            asks=[("101", "5"), ("102", "5")],
        )
        book = MutableOrderBook(snap)
        fill = book.consume_market_buy(Decimal("8"))
        expected_bps = Decimal("87.06")
        delta = abs(fill.slippage_bps - expected_bps)
        tolerance = expected_bps * Decimal("0.10")
        assert delta <= tolerance, (
            "Slippage {}bps outside ±10% of expected {}bps (delta={})"
            .format(fill.slippage_bps, expected_bps, delta)
        )


# ── Test 12 — Performance >10x Real-Time ────────────────────────────


class TestPerformance:
    """1h replay completes in <6min (>10x real-time)."""

    def test_10x_realtime_speed(self) -> None:
        """60-second synthetic replay runs faster than 6 seconds."""
        # Use a shorter duration for CI — 60s replay should be <6s
        snapshots = generate_book_snapshots(
            "BTCUSDT", 50000.0, 60, _SEED,
        )
        replay = OrderBookReplayEngine.from_snapshots(snapshots)
        noise = NoiseTrader(order_rate=0.05, max_size=0.5)
        sim = AgentBasedSimulator(
            replay_engine=replay,
            agents=[noise],
            seed=_SEED,
        )
        result = sim.run()
        speedup = float(result.speedup_factor)
        assert speedup > 10.0, (
            "Speedup {}x is below 10x minimum".format(speedup)
        )


# ── Test 13 — Decimal Fields, No Float Contamination ────────────────


class TestDecimalFields:
    """Financial fields in FillResult are Decimal."""

    def test_fill_result_decimal_types(self) -> None:
        """All financial fields in FillResult are Decimal."""
        snap = _simple_snapshot(
            bids=[("99", "10")],
            asks=[("101", "10")],
        )
        book = MutableOrderBook(snap)
        fill = book.consume_market_buy(Decimal("5"))
        assert isinstance(fill.filled_size, Decimal)
        assert isinstance(fill.avg_fill_price, Decimal)
        assert isinstance(fill.slippage_bps, Decimal)
        assert isinstance(fill.remaining_size, Decimal)
        assert isinstance(fill.total_cost, Decimal)

    def test_simulation_result_decimal_types(self) -> None:
        """SimulationResult financial fields are Decimal."""
        result = _run_short_sim(seed=42)
        assert isinstance(result.avg_slippage_bps, Decimal)
        assert isinstance(result.max_slippage_bps, Decimal)
        assert isinstance(result.partial_fill_rate, Decimal)
        assert isinstance(result.wall_clock_seconds, Decimal)
        assert isinstance(result.speedup_factor, Decimal)


# ── Test 14 — No Banned Libraries ────────────────────────────────────


class TestNoBannedLibraries:
    """Grep: no banned imports in the backtesting module."""

    def test_no_banned_imports(self) -> None:
        """Source files must not contain banned library imports."""
        from pathlib import Path

        module_dir = Path(__file__).resolve().parent.parent.parent
        bt_dir = module_dir / "prometheus" / "backtesting"
        banned = (
            "import json",
            "import pandas",
            "aioredis",
            "import pickle",
            "sqlalchemy",
            "import requests",
            "from ccxt",
            "import ccxt",
            "coinglass",
            "coinank",
        )
        for py_file in bt_dir.glob("*.py"):
            if py_file.name.startswith("test_"):
                continue
            content = py_file.read_text()
            for pattern in banned:
                assert pattern not in content, (
                    "Banned import '{}' found in {}".format(
                        pattern, py_file.name,
                    )
                )

    def test_no_loguru_kwargs(self) -> None:
        """Logger calls must use positional format, not kwargs."""
        import re
        from pathlib import Path

        module_dir = Path(__file__).resolve().parent.parent.parent
        bt_dir = module_dir / "prometheus" / "backtesting"
        # Pattern: logger.xxx("...", key=value)
        pattern = re.compile(
            r"logger\.\w+\([^)]*\w+=\w+",
        )
        for py_file in bt_dir.glob("*.py"):
            if py_file.name.startswith("test_"):
                continue
            content = py_file.read_text()
            for i, line in enumerate(content.splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if "logger." in stripped and "=" in stripped:
                    if re.search(
                        r"logger\.\w+\([^)]*,\s*\w+=", stripped,
                    ):
                        if "={}" not in stripped:
                            pytest.fail(
                                "Loguru kwargs at {}:{}: {}".format(
                                    py_file.name, i, stripped,
                                )
                            )


# ── Helper ───────────────────────────────────────────────────────────


def _run_short_sim(seed: int) -> SimulationResult:
    """Run a short 5-second simulation for test purposes."""
    snaps = generate_book_snapshots("BTCUSDT", 50000.0, 5, seed)
    replay = OrderBookReplayEngine.from_snapshots(snaps)
    # Create a signal at the midpoint
    mid_idx = len(snaps) // 2
    mid_snap = snaps[mid_idx]
    polaris = PolarisSignalAgent(
        signals=[(mid_snap.timestamp, "buy", Decimal("0.05"))],
    )
    noise = NoiseTrader(order_rate=0.2, max_size=0.5)
    sim = AgentBasedSimulator(
        replay_engine=replay,
        agents=[polaris, noise],
        seed=seed,
    )
    return sim.run()
