"""Tests for dashboard REST helpers."""

from __future__ import annotations

import pytest

from atlas.api.routes.dashboard import persist_operator_dashboard_pins
from atlas.core.autonomous_rag_analysis import DASHBOARD_PINS_REDIS_KEY


def _decode_smembers(members: set[object]) -> list[str]:
    decoded: list[str] = []
    for raw_member in members:
        if isinstance(raw_member, bytes):
            decoded.append(raw_member.decode("utf-8"))
        else:
            decoded.append(str(raw_member))
    return sorted(decoded)


@pytest.mark.asyncio
async def test_persist_dashboard_pins_normalizes_and_dedupes() -> None:
    """Operator pins merge duplicate wire formats into one canonical entry each."""
    import fakeredis.aioredis

    redis_client = fakeredis.aioredis.FakeRedis(decode_responses=False)
    stored = await persist_operator_dashboard_pins(
        redis_client,
        ["btcusdt", "BTC/USDT", "zzz-usdt", "NO_PERP/USDT"],
    )

    assert stored == 2
    raw_members = await redis_client.smembers(DASHBOARD_PINS_REDIS_KEY)
    assert _decode_smembers(set(raw_members)) == ["BTC/USDT", "ZZZ/USDT"]


@pytest.mark.asyncio
async def test_persist_dashboard_pins_clear_when_empty() -> None:
    """Empty body clears the pin set so the UI-only ladder falls back to rotation."""
    import fakeredis.aioredis

    redis_client = fakeredis.aioredis.FakeRedis(decode_responses=False)
    await redis_client.sadd(DASHBOARD_PINS_REDIS_KEY, "ETH/USDT")
    stored = await persist_operator_dashboard_pins(redis_client, [])
    assert stored == 0
    raw_members = await redis_client.smembers(DASHBOARD_PINS_REDIS_KEY)
    assert raw_members == set()
