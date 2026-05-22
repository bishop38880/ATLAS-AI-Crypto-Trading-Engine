"""Start and stop the Agent Zero nightly memory lifecycle scheduler."""

from __future__ import annotations

import asyncpg  # type: ignore[import-untyped]
from loguru import logger
from qdrant_client import AsyncQdrantClient
from redis.asyncio import Redis

from atlas.rag.agent_zero.archiver import MemoryArchiver
from atlas.rag.agent_zero.scheduler import AgentZeroScheduler
from atlas.shared.config import PolarisSettings


def start_agent_zero_scheduler(
    pool: asyncpg.Pool,
    redis: Redis,  # type: ignore[type-arg]
    qdrant: AsyncQdrantClient,
    settings: PolarisSettings,
) -> AgentZeroScheduler | None:
    """Construct and start Agent Zero when enabled and dependencies exist."""
    if not settings.agent_zero_job_enabled:
        logger.info("agent_zero_job_disabled")
        return None

    archiver = MemoryArchiver(
        pool=pool,
        redis=redis,
        qdrant=qdrant,
        settings=settings,
    )
    scheduler = AgentZeroScheduler(
        archiver=archiver,
        target_hour_utc=settings.agent_zero_hour_utc,
        redis_client=redis,
    )
    scheduler.start()
    logger.info(
        "agent_zero_scheduler_started | hour_utc={} | threshold={}",
        settings.agent_zero_hour_utc,
        settings.agent_zero_threshold,
    )
    return scheduler


async def stop_agent_zero_scheduler(
    scheduler: AgentZeroScheduler | None,
) -> None:
    """Cancel the nightly loop and wait for task completion."""
    if scheduler is None:
        return
    await scheduler.stop()
