"""Backtesting signal replay engine (BT-02)."""

from __future__ import annotations

from backtesting.engine.config import BacktestConfig, RiskConfig, ScoreThresholds
from backtesting.engine.events import ReplayEvent
from backtesting.engine.position import ClosedTrade, PositionTracker, TradeEntry, TradeExit
from backtesting.engine.replay import BacktestReplay, BacktestResult, compute_walk_forward_windows
from backtesting.engine.scorer import BarScore, BarScorer, BarScorerInput

__all__ = [
    "BacktestConfig",
    "BacktestReplay",
    "BacktestResult",
    "BarScore",
    "BarScorer",
    "BarScorerInput",
    "ClosedTrade",
    "PositionTracker",
    "ReplayEvent",
    "RiskConfig",
    "ScoreThresholds",
    "TradeEntry",
    "TradeExit",
    "compute_walk_forward_windows",
]
