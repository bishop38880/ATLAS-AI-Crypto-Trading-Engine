"""Pure Redis checks for monitoring dashboard startup sequence (no FastAPI)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import msgspec
from redis.asyncio import Redis


async def _scan_has_match(redis: Redis, pattern: str) -> bool:
    """True if at least one key matches ``pattern`` (bounded scan)."""
    async for _k in redis.scan_iter(match=pattern, count=100):
        return True
    return False


async def startup_agents_step_ok(redis: Redis) -> bool:
    """Agents are OK when Redis has status keys or latest signal proves a scoring cycle."""
    if await _scan_has_match(redis, "agent:*:status"):
        return True
    raw = await redis.get("polaris:latest_signal")
    if raw is None:
        return False
    try:
        sig: Any = msgspec.json.decode(raw)
    except Exception:
        return False
    if not isinstance(sig, Mapping):
        return False
    telemetry = sig.get("telemetry")
    if isinstance(telemetry, Mapping):
        ac = telemetry.get("agent_count")
        if isinstance(ac, int) and ac >= 1:
            return True
        ac_camel = telemetry.get("agentCount")
        if isinstance(ac_camel, int) and ac_camel >= 1:
            return True
    breakdown = sig.get("agent_breakdown")
    if breakdown is None:
        breakdown = sig.get("agentBreakdown")
    if isinstance(breakdown, Mapping) and len(breakdown) > 0:
        return True
    return False


async def startup_prices_step_ok(redis: Redis) -> bool:
    """Price feed OK when any supported provider has a cached spot snapshot.

    CoinGecko keys use Gecko ``simple/price`` ids (e.g. ``bitcoin``), not base symbols.
    """
    keys = (
        "provider:bitget:price:BTC",
        "atlas:price:BTC",
        "provider:coingecko:price:bitcoin",
    )
    for key in keys:
        if await redis.get(key) is not None:
            return True
    for pattern in ("atlas:price:*", "provider:bitget:price:*", "provider:coingecko:price:*"):
        if await _scan_has_match(redis, pattern):
            return True
    return False
