"""
Demo symbol discovery service.

Fetches the live list of Bitget SUSDT-FUTURES contracts from the
public market-data endpoint (no auth required) and caches the result
in Redis for `paper_trade_symbol_ttl_seconds`.

The frontend reads this list (via the ATLAS proxy in FE-TV-A2) to
render the symbol picker. The paper-trade executor reads it to
validate inbound instructions.
"""
import asyncio
import re
from datetime import datetime, timezone

import httpx
import msgspec
import redis.asyncio as redis_asyncio
from loguru import logger

from prometheus.services.paper_trade_models import DemoSymbol
from prometheus.settings import prometheus_settings


REDIS_CACHE_KEY = "polaris:demo_symbols:cache"

# Last-resort fallback if Bitget is fully unreachable AND the cache is
# empty. Used only on cold start during a partial outage. Validated
# manually against Bitget docs — verify before each release.
_FALLBACK_TUPLES: tuple[tuple[str, str], ...] = (
    ("SBTCSUSDT", "BTCUSDT"),
    ("SETHSUSDT", "ETHUSDT"),
    ("SXRPSUSDT", "XRPUSDT"),
)

# Demo symbol naming: leading `S` + base symbol with USDT replaced by SUSDT.
# Use a regex to extract the base — heuristic substring replace is brittle.
_DEMO_PATTERN = re.compile(r"^S([A-Z0-9]+)SUSDT$")


def demo_to_base(demo_symbol: str) -> str | None:
    """SBTCSUSDT → BTCUSDT. Returns None if pattern does not match."""
    match = _DEMO_PATTERN.fullmatch(demo_symbol)
    if not match:
        return None
    return match.group(1) + "USDT"


def base_to_demo(base_symbol: str, symbols: list[DemoSymbol]) -> DemoSymbol | None:
    """Find the DemoSymbol whose `base_symbol` matches. None if unknown."""
    for s in symbols:
        if s.base_symbol == base_symbol:
            return s
    return None


async def fetch_demo_symbols(
    redis: redis_asyncio.Redis,
    http_client: httpx.AsyncClient,
) -> list[DemoSymbol]:
    """Return the demo-symbol list. Cache → live API → fallback."""
    cached = await _read_cache(redis)
    if cached is not None:
        return cached

    fetched = await _fetch_from_bitget(http_client)
    if fetched is not None:
        await _write_cache(redis, fetched)
        return fetched

    logger.warning("demo_symbols_using_fallback | reason=cache_miss_and_api_unreachable")
    return _build_fallback()


async def _read_cache(redis: redis_asyncio.Redis) -> list[DemoSymbol] | None:
    try:
        raw = await redis.get(REDIS_CACHE_KEY)
        if raw is None:
            return None
        # Decode as list of dicts first
        data = msgspec.json.decode(raw, type=list[dict])
        return [DemoSymbol(**d) for d in data]
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("demo_symbols_cache_read_failed | exc={}", exc)
        return None


async def _write_cache(redis: redis_asyncio.Redis, symbols: list[DemoSymbol]) -> None:
    try:
        ttl = prometheus_settings.paper_trade_symbol_ttl_seconds
        # Convert Pydantic models to dicts for msgspec encoding
        encoded = msgspec.json.encode([s.model_dump() for s in symbols])
        await redis.setex(REDIS_CACHE_KEY, ttl, encoded)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("demo_symbols_cache_write_failed | exc={}", exc)


async def _fetch_from_bitget(http_client: httpx.AsyncClient) -> list[DemoSymbol] | None:
    url = f"{prometheus_settings.bitget_base_url}/api/v2/mix/market/contracts"
    params = {"productType": prometheus_settings.bitget_demo_product_type}
    timeout = prometheus_settings.bitget_symbol_fetch_timeout_s
    try:
        resp = await asyncio.wait_for(
            http_client.get(url, params=params),
            timeout=timeout,
        )
        resp.raise_for_status()
        data = msgspec.json.decode(resp.content)
        contracts = data.get("data", []) if isinstance(data, dict) else []
        result = _parse_contracts(contracts)
        logger.info("demo_symbols_fetched | count={}", len(result))
        return result if result else None
    except asyncio.CancelledError:
        raise
    except (httpx.HTTPError, asyncio.TimeoutError) as exc:
        logger.error("demo_symbols_bitget_fetch_failed | exc={}", exc)
        return None
    except Exception:
        logger.exception("demo_symbols_bitget_fetch_unexpected")
        return None


def _parse_contracts(contracts: list[dict]) -> list[DemoSymbol]:
    now = datetime.now(timezone.utc)
    out: list[DemoSymbol] = []
    for c in contracts:
        demo_sym = c.get("symbol", "")
        base = demo_to_base(demo_sym)
        if base is None:
            continue  # skip non-conforming names
        out.append(DemoSymbol(
            symbol=demo_sym,
            base_symbol=base,
            margin_coin=c.get("quoteCoin") or prometheus_settings.bitget_demo_margin_coin,
            contract_type=prometheus_settings.bitget_demo_product_type,
            fetched_at=now,
        ))
    return out


def _build_fallback() -> list[DemoSymbol]:
    now = datetime.now(timezone.utc)
    return [
        DemoSymbol(
            symbol=demo,
            base_symbol=base,
            margin_coin=prometheus_settings.bitget_demo_margin_coin,
            contract_type=prometheus_settings.bitget_demo_product_type,
            fetched_at=now,
        )
        for demo, base in _FALLBACK_TUPLES
    ]
