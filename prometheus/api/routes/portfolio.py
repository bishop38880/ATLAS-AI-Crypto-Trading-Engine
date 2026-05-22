from typing import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
from loguru import logger
from redis.asyncio import Redis

from prometheus.api._portfolio import PortfolioSnapshot, _build_portfolio_snapshot
from prometheus.settings import prometheus_settings as settings

router = APIRouter()
security = HTTPBearer()

async def get_redis() -> AsyncGenerator[Redis, None]:  # type: ignore[type-arg]
    redis = Redis.from_url(settings.redis_url)  # type: ignore[type-arg]
    try:
        yield redis
    finally:
        await redis.aclose()

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> str:
    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
        )
        return payload["sub"]
    except jwt.PyJWTError as e:
        logger.bind(reason="invalid_token", error=str(e)).info("rest_portfolio_rejected")
        raise HTTPException(status_code=401, detail="auth_required")

@router.get("/snapshot", response_model=PortfolioSnapshot, response_model_by_alias=True)
async def get_portfolio_snapshot(
    user_id: str = Depends(get_current_user),
    redis: Redis = Depends(get_redis)  # type: ignore[type-arg]
) -> PortfolioSnapshot:
    return await _build_portfolio_snapshot(user_id, redis)
