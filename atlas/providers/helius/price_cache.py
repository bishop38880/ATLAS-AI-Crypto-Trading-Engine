"""Cached SOL/JUP USD prices via CoinGecko simple price API."""

from __future__ import annotations

import time
from typing import Any

import httpx
import msgspec
from loguru import logger

_COINGECKO_SIMPLE_URL = "https://api.coingecko.com/api/v3/simple/price"
_CACHE_TTL_SECONDS: float = 60.0

_price_cache: dict[str, float | None] = {"SOL": None, "JUP": None}
_cache_ts: float = 0.0


def _decode_prices(body: bytes) -> dict[str, Any]:
    decoded: Any = msgspec.json.decode(body)
    if not isinstance(decoded, dict):
        return {}
    return decoded


async def fetch_sol_jup_prices_usd(http_client: httpx.AsyncClient) -> tuple[float | None, float | None]:
    """Return (sol_usd, jup_usd) with 60s in-process cache."""
    global _cache_ts
    now = time.time()
    if _price_cache["SOL"] is not None and (now - _cache_ts) < _CACHE_TTL_SECONDS:
        return _price_cache["SOL"], _price_cache["JUP"]

    try:
        response = await http_client.get(
            _COINGECKO_SIMPLE_URL,
            params={
                "ids": "solana,jupiter-exchange-solana",
                "vs_currencies": "usd",
            },
            timeout=5.0,
        )
        response.raise_for_status()
        raw = _decode_prices(response.content)
        sol_px = raw.get("solana", {}).get("usd")
        jup_px = raw.get("jupiter-exchange-solana", {}).get("usd")
        if sol_px is not None:
            _price_cache["SOL"] = float(sol_px)
        if jup_px is not None:
            _price_cache["JUP"] = float(jup_px)
        _cache_ts = now
    except Exception as exc:
        logger.warning("helius_price_fetch_failed | err={}", exc)

    return _price_cache["SOL"], _price_cache["JUP"]
