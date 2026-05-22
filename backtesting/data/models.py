"""Pydantic models for backtesting historical market data."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class OHLCVBar(BaseModel):
    """Single OHLCV candlestick bar."""

    model_config = ConfigDict(frozen=True)

    asset: str = Field(description="Trading pair symbol, e.g. BTCUSDT")
    timestamp_utc: str = Field(description="ISO-8601 UTC timestamp")
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal = Field(description="Base coin volume")
    volume_usd: Decimal = Field(description="Quote volume in USD")
    timeframe: str = Field(description="Candle timeframe, e.g. 1h, 4h, 1d")


class FundingRateBar(BaseModel):
    """Historical funding rate snapshot."""

    model_config = ConfigDict(frozen=True)

    asset: str
    timestamp_utc: str
    funding_rate: Decimal = Field(description="8-hour funding rate")
    funding_rate_annualised: Decimal = Field(description="rate * 3 * 365")
    open_interest_usd: Decimal | None = None


class LiquidationBar(BaseModel):
    """Aggregated liquidation data per period."""

    model_config = ConfigDict(frozen=True)

    asset: str
    timestamp_utc: str
    long_liq_usd: Decimal = Field(description="Long liquidations in period")
    short_liq_usd: Decimal = Field(description="Short liquidations in period")
    timeframe: str = Field(description="Aggregation timeframe, e.g. 1h")


class DataCoverage(BaseModel):
    """Summary of available database coverage for an asset."""

    model_config = ConfigDict(frozen=True)

    asset: str
    ohlcv_start: str | None
    ohlcv_end: str | None
    ohlcv_bar_count: int
    funding_start: str | None
    funding_end: str | None
    funding_bar_count: int
    liq_bar_count: int
    timeframes_available: list[str]
