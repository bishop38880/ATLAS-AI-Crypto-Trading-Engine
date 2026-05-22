"""Tests for paper trading engine — Session 27 Phase 0.

Covers:
    1. Market fill at last price matches Bitget behaviour
    2. Taker fee deducted from market fill proceeds
    3. Maker fee deducted for limit fills
    4. Long signal → BUY order opened
    5. Short signal → SELL order opened
    6. Close signal routes to opposite side
    7. Fee accumulation across multiple fills
    8. Position weighted average entry after multiple buys
    9. All Decimal — no float contamination

Hermetic: uses ``AsyncMock`` for all external boundaries.
No live API or database calls.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from prometheus.paper.engine import PaperTradingEngine
from prometheus.paper.models import PaperFill, PaperPosition


# ── Test 1 — Market fill at last price ────────────────────────────────


@pytest.mark.asyncio
async def test_market_fill_at_last_price() -> None:
    """Market fill executes at the provided last_price."""
    engine = PaperTradingEngine(initial_capital=Decimal("10000"))
    fill = await engine.execute_market_fill(
        symbol="BTCUSDT",
        side="buy",
        size=Decimal("0.1"),
        last_price=Decimal("50000"),
    )
    assert fill.price == Decimal("50000")
    assert fill.size == Decimal("0.1")
    assert isinstance(fill, PaperFill)


# ── Test 2 — Taker fee deducted from market fill ─────────────────────


@pytest.mark.asyncio
async def test_taker_fee_deducted_from_market_fill() -> None:
    """Market fills deduct taker fee (0.06% default)."""
    engine = PaperTradingEngine(
        initial_capital=Decimal("10000"),
        taker_fee_rate=Decimal("0.0006"),
    )
    fill = await engine.execute_market_fill(
        symbol="BTCUSDT",
        side="buy",
        size=Decimal("1"),
        last_price=Decimal("10000"),
    )
    # notional = 1 * 10000 = 10000
    # fee = 10000 * 0.0006 = 6
    assert fill.fee == Decimal("6.0000")
    assert fill.fee_rate == Decimal("0.0006")
    # net = 10000 - 6 = 9994
    assert fill.net_proceeds == Decimal("9994.0000")


# ── Test 3 — Maker fee deducted for limit fills ──────────────────────


@pytest.mark.asyncio
async def test_maker_fee_deducted_for_limit_fill() -> None:
    """Limit fills deduct maker fee (0.02% default)."""
    engine = PaperTradingEngine(
        initial_capital=Decimal("10000"),
        maker_fee_rate=Decimal("0.0002"),
    )
    fill = await engine.execute_limit_fill(
        symbol="ETHUSDT",
        side="buy",
        size=Decimal("5"),
        limit_price=Decimal("2000"),
    )
    # notional = 5 * 2000 = 10000
    # fee = 10000 * 0.0002 = 2
    assert fill.fee == Decimal("2.0000")
    assert fill.fee_rate == Decimal("0.0002")
    assert fill.net_proceeds == Decimal("9998.0000")


# ── Test 4 — Long signal → BUY order ─────────────────────────────────


@pytest.mark.asyncio
async def test_long_signal_routes_to_buy() -> None:
    """Signal 'long' must produce a BUY fill."""
    engine = PaperTradingEngine()
    fill = await engine.route_signal(
        symbol="BTCUSDT",
        signal_side="long",
        size=Decimal("0.01"),
        last_price=Decimal("60000"),
    )
    assert fill.side == "buy"
    portfolio = engine.get_portfolio()
    assert len(portfolio.positions) == 1
    assert portfolio.positions[0].side == "long"


# ── Test 5 — Short signal → SELL order ────────────────────────────────


@pytest.mark.asyncio
async def test_short_signal_routes_to_sell() -> None:
    """Signal 'short' must produce a SELL fill."""
    engine = PaperTradingEngine()
    fill = await engine.route_signal(
        symbol="ETHUSDT",
        signal_side="short",
        size=Decimal("2"),
        last_price=Decimal("3000"),
    )
    assert fill.side == "sell"
    portfolio = engine.get_portfolio()
    assert len(portfolio.positions) == 1
    assert portfolio.positions[0].side == "short"


# ── Test 6 — Close signal routes to opposite side ────────────────────


@pytest.mark.asyncio
async def test_close_signal_routes_to_opposite() -> None:
    """Close signal on a long position must produce a SELL fill."""
    engine = PaperTradingEngine()
    # Open long
    await engine.route_signal(
        symbol="BTCUSDT",
        signal_side="long",
        size=Decimal("0.1"),
        last_price=Decimal("50000"),
    )
    # Close it
    fill = await engine.route_signal(
        symbol="BTCUSDT",
        signal_side="close",
        size=Decimal("0.1"),
        last_price=Decimal("52000"),
    )
    assert fill.side == "sell"
    portfolio = engine.get_portfolio()
    # Position should be closed
    assert len(portfolio.positions) == 0


# ── Test 7 — Fee accumulation across multiple fills ──────────────────


@pytest.mark.asyncio
async def test_fee_accumulation_across_fills() -> None:
    """Total fees must accumulate across all fills."""
    engine = PaperTradingEngine(
        taker_fee_rate=Decimal("0.001"),
    )
    await engine.execute_market_fill(
        symbol="BTCUSDT", side="buy",
        size=Decimal("1"), last_price=Decimal("10000"),
    )
    await engine.execute_market_fill(
        symbol="ETHUSDT", side="buy",
        size=Decimal("10"), last_price=Decimal("2000"),
    )
    portfolio = engine.get_portfolio()
    # fee1 = 10000 * 0.001 = 10
    # fee2 = 20000 * 0.001 = 20
    assert portfolio.total_fees_paid == Decimal("30.000")
    assert portfolio.fill_count == 2


# ── Test 8 — Weighted average entry after multiple buys ──────────────


@pytest.mark.asyncio
async def test_weighted_average_entry_multiple_buys() -> None:
    """Multiple buys must produce a weighted average entry price."""
    engine = PaperTradingEngine(
        taker_fee_rate=Decimal("0"),  # zero fee for clean math
    )
    await engine.execute_market_fill(
        symbol="BTCUSDT", side="buy",
        size=Decimal("1"), last_price=Decimal("50000"),
    )
    await engine.execute_market_fill(
        symbol="BTCUSDT", side="buy",
        size=Decimal("1"), last_price=Decimal("60000"),
    )
    portfolio = engine.get_portfolio()
    btc_pos = portfolio.positions[0]
    # Total size = 2, total notional = 110000
    # Weighted avg = 110000 / 2 = 55000
    assert btc_pos.size == Decimal("2")
    assert btc_pos.entry_price == Decimal("55000")


# ── Test 9 — All Decimal, no float contamination ─────────────────────


@pytest.mark.asyncio
async def test_all_decimal_no_float() -> None:
    """All financial fields must be Decimal — no float anywhere."""
    engine = PaperTradingEngine()
    fill = await engine.execute_market_fill(
        symbol="BTCUSDT", side="buy",
        size=Decimal("0.01"), last_price=Decimal("70000"),
    )
    assert isinstance(fill.price, Decimal)
    assert isinstance(fill.size, Decimal)
    assert isinstance(fill.fee, Decimal)
    assert isinstance(fill.net_proceeds, Decimal)
    assert isinstance(fill.fee_rate, Decimal)

    portfolio = engine.get_portfolio()
    assert isinstance(portfolio.capital_usd, Decimal)
    assert isinstance(portfolio.total_fees_paid, Decimal)
    for pos in portfolio.positions:
        assert isinstance(pos.entry_price, Decimal)
        assert isinstance(pos.size, Decimal)
        assert isinstance(pos.entry_notional, Decimal)


# ── Test 10 — Anti-regression: no banned libraries ────────────────────


def test_no_banned_libraries_in_paper_module() -> None:
    """Grep: no banned imports in the paper module source files."""
    from pathlib import Path
    module_dir = Path(__file__).parent
    for py_file in module_dir.glob("*.py"):
        if py_file.name.startswith("test_"):
            continue
        content = py_file.read_text()
        for banned in ("import json", "import pandas", "aioredis",
                        "import pickle", "sqlalchemy", "import requests"):
            assert banned not in content, (
                "Banned import '{}' found in {}".format(
                    banned, py_file.name,
                )
            )
