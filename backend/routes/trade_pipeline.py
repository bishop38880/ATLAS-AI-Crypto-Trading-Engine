"""REST surface for the POLARIS gate-chain trade orchestrator."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/trade-pipeline", tags=["trade-pipeline"])


class EvaluateTradeBody(BaseModel):
    model_config = {"frozen": True}

    symbol: str = Field(min_length=1, description="Asset symbol, e.g. SOL")
    capital_usd: float = Field(default=5000.0, gt=0, description="Capital base for sizing")


@router.post("/evaluate")
async def post_evaluate_trade(
    request: Request,
    body: EvaluateTradeBody,
) -> dict[str, Any]:
    """Run gate chain → LM Studio → PROMETHEUS → position review for one asset."""
    orchestrator = getattr(request.app.state, "trade_orchestrator", None)
    if orchestrator is None:
        raise HTTPException(
            status_code=503,
            detail="trade_orchestrator_not_initialized",
        )

    symbol = body.symbol.strip().upper().split("/")[0]
    logger.info("trade_pipeline_evaluate | symbol={}", symbol)
    return await orchestrator.evaluate_and_trade(symbol, capital_usd=body.capital_usd)
