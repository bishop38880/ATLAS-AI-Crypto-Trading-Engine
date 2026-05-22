from typing import List, Optional
from fastapi import APIRouter, Request
from redis.asyncio import Redis

from atlas.api.schemas import AgentStatusPayload, ScoresPayload, PricePayload
from atlas.api._channel_reads import read_agents, read_scores, read_prices
from atlas.shared.config import PolarisSettings

router = APIRouter(prefix="/api/state", tags=["state"])

@router.get("/agents", response_model=List[AgentStatusPayload], response_model_by_alias=True)
async def get_state_agents(request: Request) -> List[AgentStatusPayload]:
    redis: Redis = request.app.state.redis
    return await read_agents(redis)

@router.get("/scores", response_model=ScoresPayload, response_model_by_alias=True)
async def get_state_scores(request: Request) -> ScoresPayload:
    redis: Redis = request.app.state.redis
    settings = PolarisSettings()
    return await read_scores(redis, settings)

@router.get("/prices", response_model=List[PricePayload], response_model_by_alias=True)
async def get_state_prices(
    request: Request,
    symbols: Optional[str] = None
) -> List[PricePayload]:
    redis: Redis = request.app.state.redis
    settings = PolarisSettings()
    return await read_prices(redis, settings, symbols)
