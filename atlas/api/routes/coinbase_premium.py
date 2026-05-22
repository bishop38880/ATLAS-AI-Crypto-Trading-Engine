"""Coinbase Premium % REST endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/api/premium", tags=["coinbase_premium"])


@router.get("/status/health")
async def get_service_status(request: Request) -> dict[str, object]:
    """WebSocket connection status for the premium service."""
    service = getattr(request.app.state, "premium_service", None)
    if service is None:
        return {"status": "not_initialized"}

    return {
        "status": "running" if service.is_healthy() else "disconnected",
        **service.status(),
    }


@router.get("/{symbol}")
async def get_premium_signals(symbol: str, request: Request) -> dict[str, Any]:
    """
    GET /api/premium/btc or /api/premium/eth — current premium and derived signals.
    """
    sym = symbol.upper()
    if sym not in ("BTC", "ETH"):
        raise HTTPException(
            status_code=400,
            detail=f"Premium index only available for BTC and ETH, not {sym}",
        )

    service = getattr(request.app.state, "premium_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Premium service not initialized")

    signals = await service.get_signals(sym)
    if signals is None:
        current = await service.get_current_premium(sym)
        return {
            "symbol": sym,
            "status": "warming_up",
            "current_premium_pct": current,
            "message": "Service connected, waiting for both feeds to populate",
        }

    return {"symbol": sym, "status": "active", **signals}
