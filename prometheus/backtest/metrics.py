"""Aggregate performance statistics from simulated trades."""

from __future__ import annotations

from decimal import Decimal

import numpy as np
import polars as pl

from prometheus.backtest.config import BacktestMetrics, SimulatedTrade


def compute_metrics(
    run_id: str,
    trades: list[SimulatedTrade],
    equity_curve: list[tuple[int, Decimal]],
    _initial_capital: Decimal,
    veto_count: int,
) -> BacktestMetrics:
    closed = [t for t in trades if t.exit_ts is not None and t.net_pnl is not None]
    total = len(closed)
    wins = [t for t in closed if (t.net_pnl or Decimal("0")) > 0]
    losses = [t for t in closed if (t.net_pnl or Decimal("0")) < 0]
    win_rate = (len(wins) / total) if total else 0.0

    z = Decimal("0")
    gross_pnl = sum(((t.gross_pnl or z) for t in closed), start=z)
    total_fees = sum(((t.fees_paid or z) for t in closed), start=z)
    net_pnl = sum(((t.net_pnl or z) for t in closed), start=z)

    max_dd, max_dd_pct = _max_drawdown_metrics(equity_curve)
    sharpe = _sharpe_ratio(equity_curve)
    sortino = _sortino_ratio(equity_curve)
    profit_factor = _profit_factor(closed)

    return BacktestMetrics(
        run_id=run_id,
        total_trades=total,
        winning_trades=len(wins),
        losing_trades=len(losses),
        win_rate=win_rate,
        gross_pnl=gross_pnl,
        total_fees=total_fees,
        net_pnl=net_pnl,
        max_drawdown=max_dd,
        max_drawdown_pct=max_dd_pct,
        sharpe_ratio=sharpe,
        sortino_ratio=sortino,
        profit_factor=profit_factor,
        avg_win=_avg_net(wins),
        avg_loss=_avg_net(losses),
        largest_win=max((w.net_pnl for w in wins if w.net_pnl), default=None),
        largest_loss=min((x.net_pnl for x in losses if x.net_pnl), default=None),
        avg_hold_seconds=_avg_hold_seconds(closed),
        veto_count=veto_count,
    )


def _avg_net(rows: list[SimulatedTrade]) -> Decimal | None:
    if not rows:
        return None
    vals = [t.net_pnl for t in rows if t.net_pnl is not None]
    if not vals:
        return None
    return sum(vals, start=Decimal("0")) / len(vals)


def _avg_hold_seconds(closed: list[SimulatedTrade]) -> float | None:
    if not closed:
        return None
    spans = [(t.exit_ts - t.entry_ts) / 1000.0 for t in closed if t.exit_ts is not None]
    return float(sum(spans) / len(spans)) if spans else None


def _max_drawdown_metrics(curve: list[tuple[int, Decimal]]) -> tuple[Decimal, float]:
    if not curve:
        return Decimal("0"), 0.0
    peak = curve[0][1]
    max_dd = Decimal("0")
    max_dd_pct = 0.0
    for _, eq in curve:
        if eq > peak:
            peak = eq
        dd = peak - eq
        if dd > max_dd:
            max_dd = dd
            max_dd_pct = float(dd / peak) if peak != 0 else 0.0
    return max_dd, max_dd_pct


def _daily_returns(curve: list[tuple[int, Decimal]]) -> list[float] | None:
    if len(curve) < 2:
        return None
    df = pl.DataFrame(
        {
            "day": [pt[0] // 86_400_000 for pt in curve],
            "ts": [pt[0] for pt in curve],
            "eq": [float(pt[1]) for pt in curve],
        },
    ).sort("ts")
    ends = df.group_by("day").agg(pl.col("eq").last()).sort("day")
    eqs = ends["eq"].to_list()
    if len(eqs) < 2:
        return None
    out: list[float] = []
    for i in range(1, len(eqs)):
        prev = eqs[i - 1]
        cur = eqs[i]
        if prev == 0:
            continue
        out.append((cur - prev) / prev)
    return out if len(out) >= 2 else None


def _sharpe_ratio(curve: list[tuple[int, Decimal]]) -> float | None:
    daily = _daily_returns(curve)
    if daily is None:
        return None
    arr = np.array(daily, dtype=np.float64)
    sig = arr.std(ddof=1)
    if sig == 0:
        return None
    mu = arr.mean()
    return float(mu / sig * np.sqrt(365))


def _sortino_ratio(curve: list[tuple[int, Decimal]]) -> float | None:
    daily = _daily_returns(curve)
    if daily is None:
        return None
    arr = np.array(daily, dtype=np.float64)
    neg = arr[arr < 0]
    if len(neg) == 0:
        return None
    dn = neg.std(ddof=1)
    if dn == 0:
        return None
    mu = arr.mean()
    return float(mu / dn * np.sqrt(365))


def _profit_factor(closed: list[SimulatedTrade]) -> float | None:
    loss_sum = Decimal("0")
    win_sum = Decimal("0")
    for t in closed:
        if t.net_pnl is None:
            continue
        if t.net_pnl < 0:
            loss_sum += t.net_pnl
        elif t.net_pnl > 0:
            win_sum += t.net_pnl
    if loss_sum == 0:
        return None
    return float(win_sum / abs(loss_sum))
