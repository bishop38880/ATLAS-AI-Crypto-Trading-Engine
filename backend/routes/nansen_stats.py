"""Nansen Stats Route — exposes credit usage and intelligence metrics.

Adheres to Sentinel v3.0 architectural invariants.
"""

from datetime import datetime, timezone
from decimal import Decimal
from fastapi import APIRouter, HTTPException
import redis.asyncio as redis
from pydantic import BaseModel

from atlas.shared.config import PolarisSettings

router = APIRouter(prefix="/api/nansen", tags=["nansen"])
settings = PolarisSettings()


class NansenUsageStats(BaseModel):
    """Credit usage statistics."""
    day_usage: int
    day_budget: int
    month_usage: int
    month_budget: int
    percent_month: float


@router.get("/stats", response_model=NansenUsageStats)
async def get_nansen_stats():
    """Retrieve current Nansen credit usage from Redis."""
    try:
        client = redis.from_url(settings.redis_url)
        now = datetime.now(timezone.utc)
        day_key = f"nansen:usage:day:{now.strftime('%Y-%m-%d')}"
        month_key = f"nansen:usage:month:{now.strftime('%Y-%m')}"

        day_usage = await client.get(day_key)
        month_usage = await client.get(month_key)

        day_val = int(day_usage) if day_usage else 0
        month_val = int(month_usage) if month_usage else 0

        # Hardcoded budgets matching server.py for now
        # In production, these would be in PolarisSettings
        day_budget = 5000
        month_budget = 100000

        await client.aclose()

        return NansenUsageStats(
            day_usage=day_val,
            day_budget=day_budget,
            month_usage=month_val,
            month_budget=month_budget,
            percent_month=round((month_val / month_budget) * 100, 2)
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch Nansen stats: {e}")
