# atlas/routes/rotation.py
import asyncio
from datetime import datetime, timezone

import redis.asyncio as redis_asyncio
from fastapi import APIRouter, Depends
from loguru import logger
from pydantic import BaseModel, ConfigDict

from atlas.dependencies import get_redis


router = APIRouter(prefix="/api/rotation", tags=["rotation"])


class RotationState(BaseModel):
    model_config = ConfigDict(frozen=True)
    full_universe: list[str]   # polaris:universe:all — mirrors ASSET_UNIVERSE (148)
    active_33: list[str]       # current 33-asset rotation
    daily_8: list[str]         # current 8-asset daily rotation
    fetched_at: datetime
    is_stale: bool             # True if any source key was missing


REDIS_KEYS = (
    "polaris:universe:all",
    "polaris:rotation:active_33",
    "polaris:rotation:daily_8",
)


@router.get("/state", response_model=RotationState)
async def get_rotation_state(
    redis: redis_asyncio.Redis = Depends(get_redis),
) -> RotationState:
    """Read three SMEMBERS lists from Redis, return as one snapshot."""
    fetched_at = datetime.now(timezone.utc)
    try:
        pipe = redis.pipeline()
        for key in REDIS_KEYS:
            pipe.smembers(key)
        raw_results = await pipe.execute()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("rotation_state_redis_failed | err={}", str(exc))
        return RotationState(
            full_universe=[],
            active_33=[],
            daily_8=[],
            fetched_at=fetched_at,
            is_stale=True,
        )

    full_raw, active_raw, daily_raw = raw_results
    is_stale = any(not r for r in raw_results)

    return RotationState(
        full_universe=sorted(_decode_set(full_raw)),
        active_33=sorted(_decode_set(active_raw)),
        daily_8=sorted(_decode_set(daily_raw)),
        fetched_at=fetched_at,
        is_stale=is_stale,
    )


def _decode_set(raw: set | list) -> list[str]:
    """Redis sets come back as set[bytes]; normalize to list[str]."""
    if not raw:
        return []
    return [m.decode() if isinstance(m, bytes) else m for m in raw]
