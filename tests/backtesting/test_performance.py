"""Tests for Phase 10: Performance Attribution.

Verifies P&L calculation, fee deduction, and drawdown tracking.
"""

from decimal import Decimal
from datetime import datetime, timezone
from prometheus.backtesting.performance import PerformanceCalculator
from prometheus.backtesting.models import TradeSlippageRecord


def test_calculator_no_trades():
    """Verify metrics for an empty trade set."""
    calc = PerformanceCalculator(initial_equity=Decimal("1000"))
    perf = calc.calculate([])
    assert perf.total_pnl == 0
    assert perf.final_equity == 1000
    assert perf.max_drawdown_pct == 0


def test_calculator_single_round_trip():
    """Verify P&L for a simple Buy-then-Sell."""
    calc = PerformanceCalculator(initial_equity=Decimal("10000"), fee_rate=Decimal("0"))
    
    trades = [
        TradeSlippageRecord(
            order_id="1",
            symbol="BTCUSDT",
            side="buy",
            requested_size=Decimal("1"),
            filled_size=Decimal("1"),
            mid_price_at_entry=Decimal("50000"),
            avg_fill_price=Decimal("50000"),
            slippage_bps=Decimal("0"),
            is_partial=False,
            timestamp=datetime.now(timezone.utc),
        ),
        TradeSlippageRecord(
            order_id="2",
            symbol="BTCUSDT",
            side="sell",
            requested_size=Decimal("1"),
            filled_size=Decimal("1"),
            mid_price_at_entry=Decimal("51000"),
            avg_fill_price=Decimal("51000"),
            slippage_bps=Decimal("0"),
            is_partial=False,
            timestamp=datetime.now(timezone.utc),
        ),
    ]
    
    perf = calc.calculate(trades)
    # Buy 1 BTC @ 50k -> -50k cash
    # Sell 1 BTC @ 51k -> +51k cash
    # Net: +1000 P&L
    assert perf.total_pnl == 1000
    assert perf.final_equity == 11000


def test_calculator_drawdown():
    """Verify max drawdown calculation."""
    # Equity curve: 1000 -> 1200 -> 800 -> 1100
    # Peak: 1200. Trough: 800. DD = (1200-800)/1200 = 33.33%
    calc = PerformanceCalculator(initial_equity=Decimal("1000"), fee_rate=Decimal("0"))
    
    # We'll mock the equity curve by submitting specific trades
    # To get 1200: Sell something at profit
    # To get 800: Buy something that drops
    # Actually, easier to just test the private method or specific outcomes
    
    trades = [
        # Win $200 (1000 -> 1200)
        TradeSlippageRecord(order_id="a", symbol="X", side="sell", requested_size=Decimal("1"), 
                            filled_size=Decimal("1"), mid_price_at_entry=Decimal("200"),
                            avg_fill_price=Decimal("200"), slippage_bps=Decimal("0"),
                            is_partial=False, timestamp=datetime.now(timezone.utc)),
        # Lose $400 (1200 -> 800)
        TradeSlippageRecord(order_id="b", symbol="X", side="buy", requested_size=Decimal("1"), 
                            filled_size=Decimal("1"), mid_price_at_entry=Decimal("400"),
                            avg_fill_price=Decimal("400"), slippage_bps=Decimal("0"),
                            is_partial=False, timestamp=datetime.now(timezone.utc)),
    ]
    
    perf = calc.calculate(trades)
    assert perf.max_drawdown_pct == Decimal("33.33")


def test_calculator_fees():
    """Verify fee deduction."""
    # 0.1% fee
    calc = PerformanceCalculator(initial_equity=Decimal("1000"), fee_rate=Decimal("0.001"))
    
    trade = TradeSlippageRecord(
        order_id="1",
        symbol="BTCUSDT",
        side="buy",
        requested_size=Decimal("1"),
        filled_size=Decimal("1"),
        mid_price_at_entry=Decimal("100"),
        avg_fill_price=Decimal("100"),
        slippage_bps=Decimal("0"),
        is_partial=False,
        timestamp=datetime.now(timezone.utc),
    )
    
    perf = calc.calculate([trade])
    # Buy $100. Fee = 100 * 0.001 = 0.1
    assert perf.total_fees == Decimal("0.1")
    assert perf.final_equity == Decimal("899.9")


def test_calculator_regimes():
    """Verify attribution to market regimes."""
    calc = PerformanceCalculator(initial_equity=Decimal("1000"), fee_rate=Decimal("0"))
    
    trades = [
        TradeSlippageRecord(order_id="1", symbol="X", side="buy", requested_size=Decimal("1"), 
                            filled_size=Decimal("1"), mid_price_at_entry=Decimal("100"),
                            avg_fill_price=Decimal("100"), slippage_bps=Decimal("0"),
                            is_partial=False, timestamp=datetime.now(timezone.utc),
                            agent_id="A", regime="trending"),
        TradeSlippageRecord(order_id="2", symbol="X", side="sell", requested_size=Decimal("1"), 
                            filled_size=Decimal("1"), mid_price_at_entry=Decimal("200"),
                            avg_fill_price=Decimal("200"), slippage_bps=Decimal("0"),
                            is_partial=False, timestamp=datetime.now(timezone.utc),
                            agent_id="A", regime="ranging"),
    ]
    
    perf = calc.calculate(trades)
    assert perf.regime_stats["trending"].total_pnl == -100
    assert perf.regime_stats["ranging"].total_pnl == 200
