"""Pydantic models for Helius Solana flow tracking."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class FlowEvent(BaseModel, frozen=True):
    """Single classified exchange flow observation."""

    timestamp: float = Field(description="Unix timestamp")
    symbol: str = Field(description="SOL or JUP")
    direction: Literal["inflow", "outflow", "transfer"]
    amount_usd: float = Field(description="Absolute USD notional")
    amount_native: float = Field(description="SOL or token amount")
    source: Literal["webhook", "poll"]
    exchange: str | None = Field(default=None, description="Exchange label if known")
    from_address: str
    to_address: str
    signature: str = Field(description="Dedup key — tx signature or composite")


class FlowSignals(BaseModel, frozen=True):
    """Scalar signals for confluence / whale on-chain scoring."""

    symbol: str
    exchange_netflow_1h: float
    exchange_netflow_4h: float
    exchange_netflow_24h: float
    whale_tx_count_1h: int
    whale_tx_count_4h: int
    whale_tx_count_24h: int
    flow_direction: Literal["inflow", "outflow", "neutral"]
    largest_single_tx_24h: float
    last_updated: str


class HeliusScoringSignals(BaseModel, frozen=True):
    """API-facing scoring payload from flow tracker."""

    exchange_netflow_24h: float
    whale_tx_count_24h: int
    flow_direction: str
    exchange_netflow_1h: float
    exchange_netflow_4h: float
    largest_single_tx_24h: float
