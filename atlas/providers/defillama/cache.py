"""DeFi Llama MCP caching layer.

Uses redis.asyncio and msgspec for JSON serialization.
"""
from typing import Any

import msgspec
import redis.asyncio as redis

from atlas.providers.defillama.models import (
    ChainTVL,
    StablecoinSupply,
    TVLSnapshot,
    YieldPool,
)


async def read_chain_tvl(r: redis.Redis, chain: str) -> ChainTVL | None:
    key = f"provider:defillama:chain_tvl:{chain}"
    raw = await r.get(key)
    if not raw:
        return None
    try:
        return msgspec.json.decode(raw, type=ChainTVL)
    except Exception as e:
        from loguru import logger
        logger.error("DeFi Llama cache decode error | error={}", str(e))
        return None


async def write_chain_tvl(r: redis.Redis, chain: str, data: ChainTVL, ttl: int) -> None:
    key = f"provider:defillama:chain_tvl:{chain}"
    payload = msgspec.json.encode(data)
    await r.setex(key, ttl, payload)


async def read_stablecoin_supply(r: redis.Redis) -> StablecoinSupply | None:
    key = "provider:defillama:stablecoin_supply"
    raw = await r.get(key)
    if not raw:
        return None
    try:
        return msgspec.json.decode(raw, type=StablecoinSupply)
    except Exception as e:
        from loguru import logger
        logger.error("DeFi Llama cache decode error | error={}", str(e))
        return None


async def write_stablecoin_supply(r: redis.Redis, data: StablecoinSupply, ttl: int) -> None:
    key = "provider:defillama:stablecoin_supply"
    payload = msgspec.json.encode(data)
    await r.setex(key, ttl, payload)


async def read_protocol_tvls(r: redis.Redis) -> list[TVLSnapshot] | None:
    key = "provider:defillama:protocol_tvls"
    raw = await r.get(key)
    if not raw:
        return None
    try:
        return msgspec.json.decode(raw, type=list[TVLSnapshot])
    except Exception as e:
        from loguru import logger
        logger.error("DeFi Llama cache decode error | error={}", str(e))
        return None


async def write_protocol_tvls(r: redis.Redis, data: list[TVLSnapshot], ttl: int) -> None:
    key = "provider:defillama:protocol_tvls"
    payload = msgspec.json.encode(data)
    await r.setex(key, ttl, payload)


async def read_yield_pools(r: redis.Redis) -> list[YieldPool] | None:
    key = "provider:defillama:yield_pools"
    raw = await r.get(key)
    if not raw:
        return None
    try:
        return msgspec.json.decode(raw, type=list[YieldPool])
    except Exception as e:
        from loguru import logger
        logger.error("DeFi Llama cache decode error | error={}", str(e))
        return None


async def write_yield_pools(r: redis.Redis, data: list[YieldPool], ttl: int) -> None:
    key = "provider:defillama:yield_pools"
    payload = msgspec.json.encode(data)
    await r.setex(key, ttl, payload)


async def write_health(r: redis.Redis, health: dict[str, Any]) -> None:
    key = "agent:defillama:status"
    payload = msgspec.json.encode(health)
    await r.set(key, payload)
