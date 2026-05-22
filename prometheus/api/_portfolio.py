import asyncio
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, ValidationError
from redis.asyncio import Redis
from loguru import logger

from prometheus.shared.models import StringDecimal, _BaseConfig

class Position(BaseModel):
    model_config = _BaseConfig
    symbol: str
    quantity: StringDecimal
    value: StringDecimal
    pnl: StringDecimal
    pnl_percent: float

class PortfolioSnapshot(BaseModel):
    model_config = _BaseConfig
    total_value: StringDecimal
    daily_pnl: StringDecimal
    daily_pnl_percent: float
    positions: tuple[Position, ...]
    is_paper: bool
    timestamp: str

def _zero_snapshot(is_paper: bool) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        total_value="0",
        daily_pnl="0",
        daily_pnl_percent=0.0,
        positions=(),
        is_paper=is_paper,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

async def _build_portfolio_snapshot(
    user_id: str, redis: Redis
) -> PortfolioSnapshot:
    raw = await redis.get(f"portfolio:{user_id}:snapshot")
    if raw is None:
        return _zero_snapshot(is_paper=True)
    try:
        return PortfolioSnapshot.model_validate_json(raw)
    except ValidationError as e:
        logger.bind(user_id=user_id, errors=e.errors()).error(
            "portfolio_snapshot_invalid"
        )
        raise
