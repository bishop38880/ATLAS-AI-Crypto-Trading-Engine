from typing import List, Optional
from fastapi import APIRouter, Request
from redis.asyncio import Redis

from atlas.api.schemas import AgentStatusPayload, PricePayload
from atlas.api._channel_reads import read_agents, read_prices
from atlas.shared.config import PolarisSettings

router = APIRouter(prefix="/api/market", tags=["market"])

@router.get("/prices", response_model=List[PricePayload], response_model_by_alias=True)
async def get_market_prices(
    request: Request,
    symbols: Optional[str] = None
) -> List[PricePayload]:
    redis: Redis = request.app.state.redis
    settings = PolarisSettings()
    return await read_prices(redis, settings, symbols)

@router.get("/agents", response_model=List[AgentStatusPayload], response_model_by_alias=True)
async def get_market_agents(request: Request) -> List[AgentStatusPayload]:
    redis: Redis = request.app.state.redis
    return await read_agents(redis)
