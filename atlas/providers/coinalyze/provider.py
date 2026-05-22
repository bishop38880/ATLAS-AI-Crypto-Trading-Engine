"""
providers/coinalyze/provider.py

Coinalyze derivatives data provider for POLARIS.

Endpoints implemented:
  1. /funding-rate          — current funding rate (all exchanges, aggregated)
  2. /open-interest         — current open interest (all exchanges, aggregated)
  3. /liquidation-history   — long/short liquidation totals per interval
  4. /long-short-ratio-history — long/short ratio per interval
"""

import asyncio
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal, Any

import msgspec

import httpx
from loguru import logger
from pydantic import BaseModel, Field
from redis.asyncio import Redis

# ------------------------------------------------------------------ #
# Models — all frozen, all Decimal for financial values
# ------------------------------------------------------------------ #

class FundingRateSnapshot(BaseModel):
    """Current aggregated funding rate across exchanges."""

    model_config = {"frozen": True}

    asset: str
    rate_pct: Decimal                    # annualised pct, e.g. Decimal("0.0100")
    raw_rate: Decimal                    # per-8h rate as returned by API
    exchange_count: int                  # how many exchanges contributed
    timestamp_utc: datetime
    status: Literal["healthy", "degraded"] = "healthy"


class OpenInterestSnapshot(BaseModel):
    """Current aggregated open interest across exchanges."""

    model_config = {"frozen": True}

    asset: str
    oi_usd: Decimal                      # total OI in USD
    exchange_count: int
    timestamp_utc: datetime
    status: Literal["healthy", "degraded"] = "healthy"


class LiquidationBar(BaseModel):
    """One interval bar of liquidation data."""

    model_config = {"frozen": True}

    timestamp_utc: datetime
    long_liq_usd: Decimal                # USD value of long liquidations
    short_liq_usd: Decimal               # USD value of short liquidations
    total_liq_usd: Decimal               # sum of both sides


class LiquidationHistory(BaseModel):
    """Liquidation history over the requested window."""

    model_config = {"frozen": True}

    asset: str
    interval: str
    bars: list[LiquidationBar]
    total_long_liq_usd: Decimal          # sum across all bars
    total_short_liq_usd: Decimal
    status: Literal["healthy", "degraded"] = "healthy"


class LongShortBar(BaseModel):
    """One interval bar of long/short ratio data."""

    model_config = {"frozen": True}

    timestamp_utc: datetime
    long_short_ratio: Decimal            # e.g. Decimal("1.45") = 59% long
    long_pct: Decimal                    # e.g. Decimal("59.2")
    short_pct: Decimal                   # e.g. Decimal("40.8")


class LongShortRatioHistory(BaseModel):
    """Long/short ratio history over the requested window."""

    model_config = {"frozen": True}

    asset: str
    interval: str
    bars: list[LongShortBar]
    current_ratio: Decimal               # most recent bar's ratio
    current_long_pct: Decimal
    status: Literal["healthy", "degraded"] = "healthy"


class CoinalyzeSnapshot(BaseModel):
    """
    Unified snapshot returned by fetch_all().
    Mirrors the legacy provider's output contract for drop-in replacement.
    """

    model_config = {"frozen": True}

    asset: str
    funding: FundingRateSnapshot
    open_interest: OpenInterestSnapshot
    liquidation_history: LiquidationHistory
    long_short: LongShortRatioHistory
    cycle_timestamp: datetime
    data_quality: Literal["full", "partial", "degraded"] = "full"
    degraded_fields: list[str] = Field(default_factory=list)


# ------------------------------------------------------------------ #
# Per-key rate limiter — one instance per API key
# ------------------------------------------------------------------ #

class CoinalyzeKeyLimiter:
    """
    Token-bucket rate limiter for one Coinalyze API key.

    Enforces 40 req/min using Redis as the distributed counter.
    Raises Exception if bucket capacity is exceeded to trigger fast-fail degradation.

    Args:
        redis_client: aioredis.Redis instance.
        key_index: Integer identifier for logging (0–3).
    """

    _MAX_PER_MIN: int = 40

    def __init__(self, redis_client: Redis, key_index: int) -> None:
        self._redis = redis_client
        self._key_index = key_index

    def _redis_key(self) -> str:
        """Redis key scoped to the current minute bucket."""
        minute_bucket = int(time.time()) // 60
        return f"ratelimit:coinalyze:key{self._key_index}:{minute_bucket}"

    async def acquire(self) -> None:
        """Block until a request slot is available for this key."""
        try:
            async with asyncio.timeout(2.0):
                redis_key = self._redis_key()
                current = await self._redis.incr(redis_key)
                if current == 1:
                    await self._redis.expire(redis_key, 60)
                if current > self._MAX_PER_MIN:
                    logger.warning(
                        "coinalyze key{} rate limit hit — fast failing",
                        self._key_index,
                    )
                    raise Exception("RateLimitExceeded")
        except Exception as exc:
            if str(exc) == "RateLimitExceeded":
                raise
            logger.warning(
                "coinalyze key{} redis limiter unavailable: {} — bypassing limiter fallback",
                self._key_index,
                exc,
            )


# ------------------------------------------------------------------ #
# Symbol mapping helpers
# ------------------------------------------------------------------ #

# Exchange codes used by Coinalyze for major derivatives venues
_EXCHANGE_CODES: list[str] = ["A", "B", "C", "0"]  # Binance, OKX, Bybit, BitMEX

_INTERVAL_MAP: dict[str, str] = {
    "1m": "1min",
    "5m": "5min",
    "15m": "15min",
    "30m": "30min",
    "1h": "1hour",
    "2h": "2hour",
    "4h": "4hour",
    "6h": "6hour",
    "12h": "12hour",
    "1d": "daily",
}


def _build_symbols(asset: str) -> str:
    """
    Convert POLARIS asset (e.g. "BTCUSDT") to Coinalyze multi-exchange
    symbol string (e.g. "BTCUSDT_PERP.A,BTCUSDT_PERP.B,...").
    """
    base = asset.upper().replace("/", "")
    if not base.endswith("USDT"):
        base = f"{base}USDT"
    return ",".join(f"{base}_PERP.{code}" for code in _EXCHANGE_CODES)


def _map_interval(polaris_interval: str) -> str:
    """Map POLARIS timeframe string to Coinalyze interval enum value."""
    mapped = _INTERVAL_MAP.get(polaris_interval)
    if not mapped:
        logger.warning(
            "coinalyze: unknown interval '{}' — defaulting to 30min",
            polaris_interval,
        )
        return "30min"
    return mapped


# ------------------------------------------------------------------ #
# Safe value constructors for degraded states
# ------------------------------------------------------------------ #

def _degraded_funding(asset: str) -> FundingRateSnapshot:
    return FundingRateSnapshot(
        asset=asset,
        rate_pct=Decimal("0"),
        raw_rate=Decimal("0"),
        exchange_count=0,
        timestamp_utc=datetime.now(timezone.utc),
        status="degraded",
    )


def _degraded_oi(asset: str) -> OpenInterestSnapshot:
    return OpenInterestSnapshot(
        asset=asset,
        oi_usd=Decimal("0"),
        exchange_count=0,
        timestamp_utc=datetime.now(timezone.utc),
        status="degraded",
    )


def _degraded_liquidations(asset: str, interval: str) -> LiquidationHistory:
    return LiquidationHistory(
        asset=asset,
        interval=interval,
        bars=[],
        total_long_liq_usd=Decimal("0"),
        total_short_liq_usd=Decimal("0"),
        status="degraded",
    )


def _degraded_long_short(asset: str, interval: str) -> LongShortRatioHistory:
    return LongShortRatioHistory(
        asset=asset,
        interval=interval,
        bars=[],
        current_ratio=Decimal("1"),
        current_long_pct=Decimal("50"),
        status="degraded",
    )


# ------------------------------------------------------------------ #
# Parse helpers — convert raw API dicts to models
# ------------------------------------------------------------------ #

def _parse_funding_responses(asset: str, responses: list[dict[str, Any]]) -> FundingRateSnapshot:
    """
    Aggregate funding rate across all exchange responses.
    Averages the per-8h rate across exchanges, converts to annualised pct.
    """
    valid_rates: list[Decimal] = []
    for item in responses:
        raw_value = item.get("value")
        if raw_value is not None:
            valid_rates.append(Decimal(str(raw_value)))

    if not valid_rates:
        return _degraded_funding(asset)

    avg_raw = sum(valid_rates, start=Decimal("0")) / Decimal(str(len(valid_rates)))
    # Annualise: (rate per 8h) * 3 intervals/day * 365 days * 100
    annualised_pct = avg_raw * Decimal("3") * Decimal("365") * Decimal("100")

    return FundingRateSnapshot(
        asset=asset,
        rate_pct=annualised_pct.quantize(Decimal("0.0001")),
        raw_rate=avg_raw.quantize(Decimal("0.000001")),
        exchange_count=len(valid_rates),
        timestamp_utc=datetime.now(timezone.utc),
        status="healthy",
    )


def _parse_oi_responses(asset: str, responses: list[dict[str, Any]]) -> OpenInterestSnapshot:
    """Sum open interest USD values across all exchange responses."""
    total_oi = Decimal("0")
    exchange_count = 0
    for item in responses:
        raw_value = item.get("value")
        if raw_value is not None:
            total_oi += Decimal(str(raw_value))
            exchange_count += 1

    if exchange_count == 0:
        return _degraded_oi(asset)

    return OpenInterestSnapshot(
        asset=asset,
        oi_usd=total_oi.quantize(Decimal("1")),
        exchange_count=exchange_count,
        timestamp_utc=datetime.now(timezone.utc),
        status="healthy",
    )


def _parse_liquidation_history(
    asset: str, interval: str, responses: list[dict[str, Any]]
) -> LiquidationHistory:
    """Merge liquidation history bars across exchanges by timestamp."""
    merged: dict[int, dict[str, Decimal]] = {}
    for item in responses:
        for b in item.get("history", []):
            ts = int(b.get("t", 0))
            if ts not in merged:
                merged[ts] = {"l": Decimal("0"), "s": Decimal("0")}
            merged[ts]["l"] += Decimal(str(b.get("l", 0)))
            merged[ts]["s"] += Decimal(str(b.get("s", 0)))

    if not merged:
        return _degraded_liquidations(asset, interval)

    bars = [
        LiquidationBar(
            timestamp_utc=datetime.fromtimestamp(ts, tz=timezone.utc),
            long_liq_usd=v["l"].quantize(Decimal("1")),
            short_liq_usd=v["s"].quantize(Decimal("1")),
            total_liq_usd=(v["l"] + v["s"]).quantize(Decimal("1")),
        ) for ts, v in sorted(merged.items())
    ]
    return LiquidationHistory(
        asset=asset, interval=interval, bars=bars,
        total_long_liq_usd=sum((b.long_liq_usd for b in bars), start=Decimal("0")),
        total_short_liq_usd=sum((b.short_liq_usd for b in bars), start=Decimal("0")),
        status="healthy",
    )


def _parse_long_short_history(
    asset: str,
    interval: str,
    responses: list[dict[str, Any]],
) -> LongShortRatioHistory:
    """Average long/short ratio bars across exchanges by timestamp."""
    merged = _merge_exchange_bars(responses)
    if not merged:
        return _degraded_long_short(asset, interval)
    bars: list[LongShortBar] = []
    for ts_unix, exchange_bars in sorted(merged.items()):
        avg_ratio = _average_decimal([b.get("r", 1) for b in exchange_bars])
        avg_long = _average_decimal([b.get("l", 50) for b in exchange_bars])
        avg_short = _average_decimal([b.get("s", 50) for b in exchange_bars])
        bars.append(LongShortBar(
            timestamp_utc=datetime.fromtimestamp(ts_unix, tz=timezone.utc),
            long_short_ratio=avg_ratio.quantize(Decimal("0.01")),
            long_pct=avg_long.quantize(Decimal("0.01")),
            short_pct=avg_short.quantize(Decimal("0.01")),
        ))
    most_recent = bars[-1]
    return LongShortRatioHistory(
        asset=asset, interval=interval, bars=bars,
        current_ratio=most_recent.long_short_ratio,
        current_long_pct=most_recent.long_pct, status="healthy",
    )


def _merge_exchange_bars(
    responses: list[dict[str, Any]],
) -> dict[int, list[dict[str, Any]]]:
    """Merge exchange bars by timestamp."""
    merged: dict[int, list[dict[str, Any]]] = {}
    for item in responses:
        for bar in item.get("history", []):
            ts = int(bar.get("t", 0))
            if ts not in merged:
                merged[ts] = []
            merged[ts].append(bar)
    return merged


def _average_decimal(values: list[Any]) -> Decimal:
    """Average a list of numeric values as Decimal. Returns Decimal('0') on empty."""
    if not values:
        return Decimal("0")
    total = sum((Decimal(str(v)) for v in values), start=Decimal("0"))
    return total / Decimal(str(len(values)))


# ------------------------------------------------------------------ #
# Main provider class
# ------------------------------------------------------------------ #

class CoinalyzeProvider:
    """
    Coinalyze derivatives data provider.
    Uses 4 dedicated API keys to avoid hitting limits across endpoints.
    """

    _BASE_URL: str = "https://api.coinalyze.net/v1"
    _TIMEOUT: float = 10.0

    # Redis TTLs per endpoint (seconds)
    _TTL_FUNDING: int = 30
    _TTL_OI: int = 30
    _TTL_LIQUIDATIONS: int = 60
    _TTL_LONG_SHORT: int = 60

    def __init__(
        self,
        settings: Any,
        redis_client: Redis,
        http_client: httpx.AsyncClient,
    ) -> None:
        self._redis = redis_client
        self._http = http_client

        # One API key per endpoint — read from PolarisSettings
        self._key_funding: str = settings.coinalyze_key_funding
        self._key_oi: str = settings.coinalyze_key_oi
        self._key_liquidations: str = settings.coinalyze_key_liquidations
        self._key_long_short: str = settings.coinalyze_key_long_short

        # One rate limiter per key
        self._limiter_funding = CoinalyzeKeyLimiter(redis_client, key_index=0)
        self._limiter_oi = CoinalyzeKeyLimiter(redis_client, key_index=1)
        self._limiter_liquidations = CoinalyzeKeyLimiter(redis_client, key_index=2)
        self._limiter_long_short = CoinalyzeKeyLimiter(redis_client, key_index=3)

    # ---------------------------------------------------------------- #
    # Public API
    # ---------------------------------------------------------------- #

    async def fetch_funding_rate(self, asset: str) -> FundingRateSnapshot:
        cache_key = f"coinalyze:{asset}:funding"
        cached = await self._get_cached(cache_key, FundingRateSnapshot)
        if cached:
            return cached

        try:
            await self._limiter_funding.acquire()
        except Exception as exc:
            logger.warning("coinalyze: funding limit error: {}", exc)
            return _degraded_funding(asset)

        symbols = _build_symbols(asset)
        raw = await self._get(
            endpoint="/funding-rate",
            api_key=self._key_funding,
            params={"symbols": symbols},
            asset=asset,
        )
        if raw is None:
            return _degraded_funding(asset)

        result = _parse_funding_responses(asset, raw)
        await self._set_cached(cache_key, result, self._TTL_FUNDING)
        return result

    async def fetch_open_interest(self, asset: str) -> OpenInterestSnapshot:
        cache_key = f"coinalyze:{asset}:oi"
        cached = await self._get_cached(cache_key, OpenInterestSnapshot)
        if cached:
            return cached

        try:
            await self._limiter_oi.acquire()
        except Exception as exc:
            logger.warning("coinalyze: oi limit error: {}", exc)
            return _degraded_oi(asset)

        symbols = _build_symbols(asset)
        raw = await self._get(
            endpoint="/open-interest",
            api_key=self._key_oi,
            params={"symbols": symbols, "convert_to_usd": "true"},
            asset=asset,
        )
        if raw is None:
            return _degraded_oi(asset)

        result = _parse_oi_responses(asset, raw)
        await self._set_cached(cache_key, result, self._TTL_OI)
        return result

    async def fetch_liquidation_history(
        self,
        asset: str,
        interval: str = "30m",
        lookback_hours: int = 4,
    ) -> LiquidationHistory:
        cache_key = f"coinalyze:{asset}:liquidations:{interval}"
        cached = await self._get_cached(cache_key, LiquidationHistory)
        if cached:
            return cached

        try:
            await self._limiter_liquidations.acquire()
        except Exception as exc:
            logger.warning("coinalyze: liquidations limit error: {}", exc)
            return _degraded_liquidations(asset, interval)

        now = int(time.time())
        from_ts = now - (lookback_hours * 3600)
        coinalyze_interval = _map_interval(interval)
        symbols = _build_symbols(asset)

        raw = await self._get(
            endpoint="/liquidation-history",
            api_key=self._key_liquidations,
            params={
                "symbols": symbols,
                "interval": coinalyze_interval,
                "from": from_ts,
                "to": now,
                "convert_to_usd": "true",
            },
            asset=asset,
        )
        if raw is None:
            return _degraded_liquidations(asset, interval)

        result = _parse_liquidation_history(asset, interval, raw)
        await self._set_cached(cache_key, result, self._TTL_LIQUIDATIONS)
        return result

    async def fetch_long_short_ratio(
        self,
        asset: str,
        interval: str = "30m",
        lookback_hours: int = 4,
    ) -> LongShortRatioHistory:
        cache_key = f"coinalyze:{asset}:long_short:{interval}"
        cached = await self._get_cached(cache_key, LongShortRatioHistory)
        if cached:
            return cached

        try:
            await self._limiter_long_short.acquire()
        except Exception as exc:
            logger.warning("coinalyze: long_short limit error: {}", exc)
            return _degraded_long_short(asset, interval)

        now = int(time.time())
        from_ts = now - (lookback_hours * 3600)
        coinalyze_interval = _map_interval(interval)
        symbols = _build_symbols(asset)

        raw = await self._get(
            endpoint="/long-short-ratio-history",
            api_key=self._key_long_short,
            params={
                "symbols": symbols,
                "interval": coinalyze_interval,
                "from": from_ts,
                "to": now,
            },
            asset=asset,
        )
        if raw is None:
            return _degraded_long_short(asset, interval)

        result = _parse_long_short_history(asset, interval, raw)
        await self._set_cached(cache_key, result, self._TTL_LONG_SHORT)
        return result

    async def fetch_all(
        self,
        asset: str,
        interval: str = "30m",
    ) -> CoinalyzeSnapshot:
        """
        Fetch all four endpoints concurrently and return a unified snapshot.
        Uses return_exceptions=True to prevent single-endpoint failures
        from crashing the entire snapshot.
        """
        results = await asyncio.gather(
            self.fetch_funding_rate(asset),
            self.fetch_open_interest(asset),
            self.fetch_liquidation_history(asset, interval),
            self.fetch_long_short_ratio(asset, interval),
            return_exceptions=True,
        )

        funding = results[0] if not isinstance(results[0], BaseException) else _degraded_funding(asset)
        oi = results[1] if not isinstance(results[1], BaseException) else _degraded_oi(asset)
        liquidations = results[2] if not isinstance(results[2], BaseException) else _degraded_liquidations(asset, interval)
        long_short = results[3] if not isinstance(results[3], BaseException) else _degraded_long_short(asset, interval)

        for i, r in enumerate(results):
            if isinstance(r, BaseException):
                logger.error("coinalyze: fetch_all slot {} raised: {}", i, r)

        degraded_fields = _collect_degraded_fields(funding, oi, liquidations, long_short)
        quality = _assess_quality(degraded_fields)

        return CoinalyzeSnapshot(
            asset=asset,
            funding=funding,
            open_interest=oi,
            liquidation_history=liquidations,
            long_short=long_short,
            cycle_timestamp=datetime.now(timezone.utc),
            data_quality=quality,
            degraded_fields=degraded_fields,
        )

    async def mark_degraded(self, reason: str) -> None:
        """Write DEGRADED status to Redis for health monitoring."""
        try:
            async with asyncio.timeout(2.0):
                await self._redis.setex("provider:coinalyze:status", 120, "DEGRADED")
                logger.warning("coinalyze: marked DEGRADED — {}", reason)
        except Exception as exc:
            logger.error("coinalyze: could not write DEGRADED status: {}", exc)

    # ---------------------------------------------------------------- #
    # Private HTTP helper
    # ---------------------------------------------------------------- #

    async def _get(
        self,
        endpoint: str,
        api_key: str,
        params: dict[str, Any],
        asset: str,
    ) -> list[dict[str, Any]] | None:
        """Execute a single GET request against the Coinalyze API."""
        url = f"{self._BASE_URL}{endpoint}"
        headers = {"api_key": api_key}
        try:
            response = await self._http.get(
                url,
                headers=headers,
                params=params,
                timeout=self._TIMEOUT,
            )
            return await self._handle_response_status(
                response, endpoint, asset,
            )
        except httpx.TimeoutException:
            logger.error("coinalyze: timeout on {} for {}", endpoint, asset)
            return None
        except Exception as exc:
            logger.error("coinalyze: unexpected error on {} for {}: {}", endpoint, asset, exc)
            return None

    async def _handle_response_status(
        self,
        response: httpx.Response,
        endpoint: str,
        asset: str,
    ) -> list[dict[str, Any]] | None:
        """Handle HTTP status codes from API response."""
        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", 60))
            logger.warning(
                "coinalyze: 429 on {} for {} — Retry-After {}s (fast failing)",
                endpoint, asset, retry_after,
            )
            return None
        if response.status_code == 401:
            logger.error("coinalyze: 401 invalid API key on {}", endpoint)
            await self.mark_degraded(f"401 on {endpoint}")
            return None
        if response.status_code != 200:
            logger.error(
                "coinalyze: {} returned {} for {}",
                endpoint, response.status_code, asset,
            )
            return None
        return msgspec.json.decode(response.content)

    # ---------------------------------------------------------------- #
    # Redis cache helpers
    # ---------------------------------------------------------------- #

    async def _get_cached(self, key: str, model_class: type[Any]) -> Any | None:
        """Return cached Pydantic model or None on miss/error.

        Uses model_construct() to skip re-validation on trusted cached data.
        """
        try:
            async with asyncio.timeout(2.0):
                raw = await self._redis.get(key)
                if raw:
                    data = msgspec.json.decode(raw)
                    return model_class.model_construct(**data)
        except Exception as exc:
            logger.warning("coinalyze: cache read failed for {}: {}", key, exc)
        return None

    async def _set_cached(self, key: str, model: BaseModel, ttl: int) -> None:
        """Serialise Pydantic model to Redis with TTL. Uses msgspec."""
        try:
            async with asyncio.timeout(2.0):
                payload = msgspec.json.encode(model.model_dump(mode="json"))
                await self._redis.setex(key, ttl, payload)
        except Exception as exc:
            logger.warning("coinalyze: cache write failed for {}: {}", key, exc)


# ------------------------------------------------------------------ #
# Quality assessment helpers
# ------------------------------------------------------------------ #

def _collect_degraded_fields(
    funding: FundingRateSnapshot,
    oi: OpenInterestSnapshot,
    liquidations: LiquidationHistory,
    long_short: LongShortRatioHistory,
) -> list[str]:
    """Return list of field names that are in degraded status."""
    degraded: list[str] = []
    if funding.status == "degraded":
        degraded.append("funding")
    if oi.status == "degraded":
        degraded.append("open_interest")
    if liquidations.status == "degraded":
        degraded.append("liquidation_history")
    if long_short.status == "degraded":
        degraded.append("long_short")
    return degraded


def _assess_quality(
    degraded_fields: list[str],
) -> Literal["full", "partial", "degraded"]:
    """Map number of degraded fields to an overall quality label."""
    count = len(degraded_fields)
    if count == 0:
        return "full"
    if count <= 2:
        return "partial"
    return "degraded"
