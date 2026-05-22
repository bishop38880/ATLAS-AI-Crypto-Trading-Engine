"""Tests for Agent Zero lifecycle API payload builder."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import msgspec
import pytest

from atlas.api.agent_zero_lifecycle_logic import fetch_agent_zero_lifecycle_payload
from atlas.shared.config import PolarisSettings


@pytest.mark.asyncio()
async def test_lifecycle_uses_redis_schedule_and_threshold() -> None:
    """Schedule metadata from Redis drives next-run fields."""
    redis = AsyncMock()
    schedule = {
        "nextRunIso": "2026-05-21T02:00:00+00:00",
        "nextRunRelative": "in 5h 0m",
        "lastRun": {
            "timestamp": "2026-05-20T02:00:00+00:00",
            "status": "PASSED",
            "documentsScored": 10,
            "archived": 2,
            "retained": 8,
            "errors": 0,
        },
    }
    redis.get = AsyncMock(return_value=msgspec.json.encode(schedule))

    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)

    settings = PolarisSettings(agent_zero_threshold=0.75)
    payload = await fetch_agent_zero_lifecycle_payload(redis, pool, settings)

    assert payload["next_run_relative"] == "in 5h 0m"
    assert payload["threshold"] == 0.75
    assert payload["last_run_results"]["documentsScored"] == 10
    assert payload["last_run_results"]["archived"] == 2


@pytest.mark.asyncio()
async def test_lifecycle_falls_back_to_archive_log() -> None:
    """When Redis lastRun is missing, use rag_archive_log row."""
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)

    run_at = datetime(2026, 5, 19, 2, 0, tzinfo=timezone.utc)
    log_row = {
        "run_at": run_at,
        "total_scored": 5,
        "archived_count": 1,
        "retained_count": 4,
        "avg_escore": 0.82,
        "error_count": 0,
    }
    dist_row = {
        "band_0": 0,
        "band_1": 1,
        "band_2": 1,
        "band_3": 3,
        "avg_escore": 0.82,
        "scored_total": 5,
    }
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(side_effect=[log_row, dist_row])

    payload = await fetch_agent_zero_lifecycle_payload(
        redis,
        pool,
        PolarisSettings(),
    )

    assert payload["last_run_iso"] == run_at.isoformat()
    assert payload["last_run_results"]["retained"] == 4
    assert len(payload["escore_distribution"]) == 4
    assert payload["collection_health_label"] == "HEALTHY"


@pytest.mark.asyncio()
async def test_lifecycle_without_pool_returns_awaiting_data() -> None:
    """No Postgres pool yields empty distribution and awaiting label."""
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)

    payload = await fetch_agent_zero_lifecycle_payload(
        redis,
        None,
        PolarisSettings(),
    )

    assert payload["escore_distribution"] == []
    assert payload["collection_health_label"] == "AWAITING DATA"
    assert payload["last_run_relative"] == "Never"
