"""Backtest engine configuration models."""

from __future__ import annotations

import uuid
from decimal import Decimal

from pydantic import BaseModel, Field


class ScoreThresholds(BaseModel, frozen=True):
    """Confluence score thresholds for signal classification."""

    strong: int = 180
    buy: int = 150
    weak: int = 120
    no_trade: int = 0


class RiskConfig(BaseModel, frozen=True):
    """Risk management parameters."""

    account_size_usd: Decimal = Decimal("10000")
    risk_per_trade_pct: Decimal = Decimal("2.0")
    max_position_pct: Decimal = Decimal("5.0")
    max_portfolio_exposure_pct: Decimal = Decimal("25.0")
    stop_loss_atr_multiple: Decimal = Decimal("2.0")
    take_profit_atr_multiple: Decimal = Decimal("4.0")
    max_leverage: int = 10
    slippage_bps: int = 10
    fee_bps: int = 6


class BacktestConfig(BaseModel, frozen=True):
    """Complete configuration for a single backtest run."""

    run_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    assets: list[str] = Field(default_factory=lambda: ["BTCUSDT"])
    start_date: str = "2024-01-01"
    end_date: str = "2025-01-01"
    timeframe: str = "1h"
    higher_timeframe: str = "4h"
    thresholds: ScoreThresholds = Field(default_factory=ScoreThresholds)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    use_synthetic: bool = False
    walk_forward: bool = False
    walk_forward_train_bars: int = 720
    walk_forward_test_bars: int = 240
    live_signal_bonus: int = Field(
        default=0,
        ge=0,
        le=50,
        description="Optional fixed bonus simulating whale/sentiment dimensions.",
    )
    verbose_events: bool = False
    description: str = ""
