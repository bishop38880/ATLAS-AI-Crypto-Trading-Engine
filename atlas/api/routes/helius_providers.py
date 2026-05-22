"""Helius provider REST — on-chain flow signals for SOL/JUP confluence scoring."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from atlas.api.schemas import _BaseConfig
from atlas.core.registry import helius_provider
from atlas.providers.helius.solana_addresses import HELIUS_ENABLED_SYMBOLS
from atlas.services import solana_flow_tracker

router = APIRouter(prefix="/api/providers", tags=["helius"])


class HeliusFlowSignalsPayload(BaseModel):
    """Scalar flow signals from SolanaFlowTracker rolling windows."""

    model_config = _BaseConfig

    exchange_netflow_1h: float = Field(description="USD netflow over 1h; positive = exchange inflow")
    exchange_netflow_4h: float
    exchange_netflow_24h: float
    whale_tx_count_1h: int
    whale_tx_count_4h: int
    whale_tx_count_24h: int
    flow_direction: str
    largest_single_tx_24h: float


class HeliusSignalsResponse(BaseModel):
    """Wire format for GET /api/providers/helius/signals/{symbol}."""

    model_config = _BaseConfig

    symbol: str
    status: str
    signals: HeliusFlowSignalsPayload | None = None
    last_updated: str | None = None


class HeliusHealthResponse(BaseModel):
    """Wire format for GET /api/providers/health/helius."""

    model_config = _BaseConfig

    id: str = "helius"
    name: str = "Helius"
    status: str
    configured: bool
    error: str | None = None


@router.get("/helius/signals/{symbol}", response_model=HeliusSignalsResponse)
async def get_helius_signals(symbol: str) -> HeliusSignalsResponse:
    """Pre-computed Solana exchange flow signals for confluence / whale scoring."""
    symbol_upper = symbol.upper()
    if symbol_upper not in HELIUS_ENABLED_SYMBOLS:
        raise HTTPException(
            status_code=400,
            detail=f"Helius only serves SOL and JUP, not {symbol_upper}",
        )

    flow = await solana_flow_tracker.get_signals(symbol_upper)
    if flow is None:
        return HeliusSignalsResponse(symbol=symbol_upper, status="no_data", signals=None)

    return HeliusSignalsResponse(
        symbol=symbol_upper,
        status="active",
        signals=HeliusFlowSignalsPayload(
            exchange_netflow_1h=flow.exchange_netflow_1h,
            exchange_netflow_4h=flow.exchange_netflow_4h,
            exchange_netflow_24h=flow.exchange_netflow_24h,
            whale_tx_count_1h=flow.whale_tx_count_1h,
            whale_tx_count_4h=flow.whale_tx_count_4h,
            whale_tx_count_24h=flow.whale_tx_count_24h,
            flow_direction=flow.flow_direction,
            largest_single_tx_24h=flow.largest_single_tx_24h,
        ),
        last_updated=flow.last_updated,
    )


@router.get("/health/helius", response_model=HeliusHealthResponse)
async def get_helius_health() -> HeliusHealthResponse:
    """Connectivity probe for Helius (requires HELIUS_API_KEY)."""
    provider = helius_provider
    if provider is None or not provider.is_configured:
        return HeliusHealthResponse(
            status="offline",
            configured=False,
            error="missing_api_key",
        )

    ok, err = await provider._check_connectivity()
    if ok:
        provider.mark_healthy()
        return HeliusHealthResponse(status="active", configured=True, error=None)

    provider.mark_degraded(err or "connectivity_failed")
    return HeliusHealthResponse(
        status="degraded",
        configured=True,
        error=err,
    )
