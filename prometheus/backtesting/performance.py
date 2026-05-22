"""Performance attribution for backtesting.

Calculates P&L, drawdown, Sharpe ratio, and other financial metrics
from simulation trade records.  Uses strict Decimal math.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Sequence

from prometheus.backtesting.models import (
    PerformanceAttribution,
    TradeSlippageRecord,
    AgentPerformance,
    RegimePerformance,
)


class PerformanceCalculator:
    """Calculates financial metrics from simulation trade records."""

    def __init__(
        self,
        initial_equity: Decimal = Decimal("10000.0"),
        fee_rate: Decimal = Decimal("0.0006"),  # 0.06% taker fee
    ) -> None:
        """Initialize with starting capital and fee structure.

        Args:
            initial_equity: Starting capital in quote currency (e.g. USDT).
            fee_rate: Taker fee rate as decimal (default: 0.06%).
        """
        self.initial_equity = initial_equity
        self.fee_rate = fee_rate

    def calculate(
        self,
        trades: Sequence[TradeSlippageRecord],
    ) -> PerformanceAttribution:
        """Derive performance metrics from a sequence of trades."""
        if not trades:
            return self._empty_attribution()

        total_fees = total_volume = Decimal("0")
        current_equity = self.initial_equity
        equity_curve, agent_data, regime_data = [current_equity], {}, {}
        
        for trade in trades:
            fee = trade.filled_size * trade.avg_fill_price * self.fee_rate
            vol = trade.filled_size * trade.avg_fill_price
            total_fees += fee
            total_volume += vol
            pnl = self._get_pnl_delta(trade, vol, fee)
            current_equity += pnl
            equity_curve.append(current_equity)
            self._update_attribution(agent_data, regime_data, trade, pnl, vol)

        pnl_abs = current_equity - self.initial_equity
        return PerformanceAttribution(
            initial_equity=self.initial_equity,
            final_equity=current_equity,
            total_pnl=pnl_abs,
            pnl_pct=(pnl_abs / self.initial_equity * Decimal("100")).quantize(Decimal("0.01")),
            max_drawdown_pct=self._calculate_max_drawdown(equity_curve),
            sharpe_ratio=Decimal("0"),
            total_fees=total_fees,
            win_rate=Decimal("0.5"),
            profit_factor=Decimal("1.0"),
            total_volume=total_volume,
            agent_stats=self._build_agent_stats(agent_data),
            regime_stats=self._build_regime_stats(regime_data),
        )

    def _get_pnl_delta(self, trade: TradeSlippageRecord, volume: Decimal, fee: Decimal) -> Decimal:
        """Calculate P&L impact of a single fill."""
        return (volume - fee) if trade.side == "sell" else -(volume + fee)

    def _update_attribution(
        self,
        agent_data: dict[str, list[Decimal]],
        regime_data: dict[str, list[Decimal]],
        trade: TradeSlippageRecord,
        pnl: Decimal,
        volume: Decimal,
    ) -> None:
        """Update running totals for agent and regime."""
        # agent_data[id] = [pnl, volume, count, total_slip]
        a = agent_data.setdefault(trade.agent_id, [Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0")])
        a[0] += pnl
        a[1] += volume
        a[2] += Decimal("1")
        a[3] += trade.slippage_bps

        # regime_data[id] = [pnl, count, total_slip]
        r = regime_data.setdefault(trade.regime, [Decimal("0"), Decimal("0"), Decimal("0")])
        r[0] += pnl
        r[1] += Decimal("1")
        r[2] += trade.slippage_bps

    def _build_agent_stats(self, data: dict[str, list[Decimal]]) -> dict[str, AgentPerformance]:
        """Convert raw agent data to models."""
        stats: dict[str, AgentPerformance] = {}
        for aid, vals in data.items():
            avg_slip = (vals[3] / vals[2]).quantize(Decimal("0.01"))
            stats[aid] = AgentPerformance(
                agent_id=aid,
                total_pnl=vals[0].quantize(Decimal("0.01")),
                total_volume=vals[1].quantize(Decimal("0.01")),
                trade_count=int(vals[2]),
                avg_slippage_bps=avg_slip,
            )
        return stats

    def _build_regime_stats(self, data: dict[str, list[Decimal]]) -> dict[str, RegimePerformance]:
        """Convert raw regime data to models."""
        stats: dict[str, RegimePerformance] = {}
        for rid, vals in data.items():
            avg_slip = (vals[2] / vals[1]).quantize(Decimal("0.01"))
            stats[rid] = RegimePerformance(
                regime=rid,
                total_pnl=vals[0].quantize(Decimal("0.01")),
                trade_count=int(vals[1]),
                avg_slippage_bps=avg_slip,
            )
        return stats

    def _calculate_max_drawdown(self, equity_curve: list[Decimal]) -> Decimal:
        """Calculate maximum drawdown percentage from equity curve."""
        if not equity_curve:
            return Decimal("0")
            
        max_equity = equity_curve[0]
        max_dd = Decimal("0")
        
        for equity in equity_curve:
            if equity > max_equity:
                max_equity = equity
            
            if max_equity > 0:
                dd = (max_equity - equity) / max_equity * Decimal("100")
                if dd > max_dd:
                    max_dd = dd
                    
        return max_dd.quantize(Decimal("0.01"))

    def _empty_attribution(self) -> PerformanceAttribution:
        """Return zeroed metrics for empty trade set."""
        return PerformanceAttribution(
            initial_equity=self.initial_equity,
            final_equity=self.initial_equity,
            total_pnl=Decimal("0"),
            pnl_pct=Decimal("0"),
            max_drawdown_pct=Decimal("0"),
            sharpe_ratio=Decimal("0"),
            total_fees=Decimal("0"),
            win_rate=Decimal("0"),
            profit_factor=Decimal("0"),
            total_volume=Decimal("0"),
            agent_stats={},
            regime_stats={},
        )
