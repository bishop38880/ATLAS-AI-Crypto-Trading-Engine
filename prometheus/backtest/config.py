"""Backtest configuration and immutable row models.

Project ``PolarisSettings`` (ATLAS) MUST NOT be imported here — PROMETHEUS backtester
hard wall. ``shared/signal_schema.py`` was absent in-repo; ``SignalRow`` is defined
locally. ``BacktestConfig`` mirrors pydantic-settings (``POLARIS_BT_*`` env prefix).
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import msgspec
from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class BacktestConfig(BaseSettings):
    """Loaded via pydantic-settings — environment via POLARIS_BT_* fields only."""

    model_config = SettingsConfigDict(
        env_prefix="POLARIS_BT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    db_path: Path = Field(
        default=Path("prometheus/backtest/polaris_backtest.duckdb"),
        description="DuckDB file for offline replay",
    )
    initial_capital_usd: Decimal = Decimal("10000")
    risk_per_trade_pct: Decimal = Decimal("0.01")
    max_open_positions: int = 3
    maker_fee_bps: Decimal = Decimal("2")
    taker_fee_bps: Decimal = Decimal("6")
    slippage_bps: Decimal = Decimal("5")
    default_stop_distance_pct: Decimal = Decimal("0.02")
    score_threshold: float = 65.0
    min_confidence: float = 0.55


class CandleRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    asset: str
    timeframe: str
    ts: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


class SignalRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    signal_id: str
    asset: str
    ts: int
    direction: str
    action: str
    total_score: Decimal
    confidence: Decimal
    risk_veto: bool
    ttl_seconds: int | None
    raw_json: str


class SimulatedTrade(BaseModel):
    model_config = ConfigDict(frozen=True)

    trade_id: str
    run_id: str
    signal_id: str
    asset: str
    direction: str
    entry_ts: int
    exit_ts: int | None
    entry_price: Decimal
    exit_price: Decimal | None
    size_base: Decimal
    notional_usd: Decimal
    stop_price: Decimal | None
    gross_pnl: Decimal | None
    fees_paid: Decimal | None
    net_pnl: Decimal | None
    exit_reason: str | None
    risk_veto: bool
    ttl_seconds: int | None = None


class BacktestMetrics(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    gross_pnl: Decimal
    total_fees: Decimal
    net_pnl: Decimal
    max_drawdown: Decimal
    max_drawdown_pct: float
    sharpe_ratio: float | None
    sortino_ratio: float | None
    profit_factor: float | None
    avg_win: Decimal | None
    avg_loss: Decimal | None
    largest_win: Decimal | None
    largest_loss: Decimal | None
    avg_hold_seconds: float | None
    veto_count: int


class BacktestRun(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    created_at: int
    asset: str
    timeframe: str
    start_ts: int
    end_ts: int
    initial_capital: Decimal
    maker_fee_bps: Decimal
    taker_fee_bps: Decimal
    slippage_bps: Decimal
    config_json: str


class BacktestResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    metrics: BacktestMetrics
    trades: list[SimulatedTrade]
    equity_curve: list[tuple[int, Decimal]]
    veto_count: int


def backtest_config_json(config: BacktestConfig) -> str:
    """Serialise config for ``bt_runs.config_json``."""
    payload: dict[str, Any] = {
        "db_path": str(config.db_path),
        "initial_capital_usd": str(config.initial_capital_usd),
        "risk_per_trade_pct": str(config.risk_per_trade_pct),
        "max_open_positions": config.max_open_positions,
        "maker_fee_bps": str(config.maker_fee_bps),
        "taker_fee_bps": str(config.taker_fee_bps),
        "slippage_bps": str(config.slippage_bps),
        "default_stop_distance_pct": str(config.default_stop_distance_pct),
        "score_threshold": config.score_threshold,
        "min_confidence": config.min_confidence,
    }
    return msgspec.json.encode(payload).decode()
