"""CoinGecko hourly source with dual-key sequential failover."""

from __future__ import annotations

import time
from decimal import Decimal

import httpx
import msgspec
from loguru import logger

from atlas.core.circuit_breaker import get_circuit_breaker
from atlas.monitoring.key_pool import ApiKeySlot, FailoverKeyPool
from atlas.monitoring.metrics import record_fetch_outcome
from atlas.monitoring.retry_policy import fetch_with_backoff
from atlas.shared.coingecko_symbol_map import COINGECKO_SIMPLE_PRICE_ID_BY_BASE
from atlas.shared.config import PolarisSettings

_COINGECKO_BASE = "https://api.coingecko.com/api/v3"


class CoinGeckoHourlySource:
    """Fetch spot reference; primary key until rate-limited, then secondary."""

    def __init__(
        self,
        settings: PolarisSettings,
        http_client: httpx.AsyncClient,
        key_pool: FailoverKeyPool,
        redis_client: object,
    ) -> None:
        self._settings = settings
        self._http = http_client
        self._keys = key_pool
        self._redis = redis_client

    def _breaker(self, key_id: str) -> object:
        return get_circuit_breaker(f"coingecko:{key_id}")

    async def fetch_spot(
        self,
        asset_base: str,
    ) -> tuple[Decimal, Decimal | None, Decimal | None, str, float, str]:
        """
        Returns ``(price, volume_24h, market_cap, key_id, latency_ms, status)``.
        """
        slug = COINGECKO_SIMPLE_PRICE_ID_BY_BASE.get(asset_base.upper())
        if slug is None:
            logger.warning("monitoring_coingecko_unknown_slug | asset={}", asset_base)
            return Decimal("0"), None, None, "unknown", 0.0, "failed"

        now = time.monotonic()
        keys_to_try = self._keys.eligible_keys(now)
        primary_key_id = self._keys.key_ids[0] if self._keys.key_ids else ""
        last_error: Exception | None = None

        for key in keys_to_try:
            used_failover = bool(primary_key_id) and key.key_id != primary_key_id
            try:
                return await self._fetch_spot_with_key(
                    asset_base,
                    slug,
                    key,
                    failover=used_failover,
                )
            except httpx.HTTPStatusError as exc:
                last_error = exc
                if exc.response.status_code == 429:
                    cooldown_until = now + self._settings.hourly_monitor_key_cooldown_s
                    self._keys.mark_cooldown(key.key_id, cooldown_until)
                    logger.warning(
                        "monitoring_coingecko_key_throttled | asset={} | key={} | failover=next",
                        asset_base,
                        key.key_id,
                    )
                    continue
                raise
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "monitoring_coingecko_key_failed | asset={} | key={} | err={}",
                    asset_base,
                    key.key_id,
                    exc,
                )
                continue

        if last_error is not None:
            raise last_error
        raise RuntimeError("monitoring_coingecko_all_keys_exhausted")

    async def _fetch_spot_with_key(
        self,
        asset_base: str,
        slug: str,
        key: ApiKeySlot,
        *,
        failover: bool = False,
    ) -> tuple[Decimal, Decimal | None, Decimal | None, str, float, str]:
        breaker = self._breaker(key.key_id)

        async def _do_fetch() -> tuple[Decimal, Decimal | None, Decimal | None]:
            headers: dict[str, str] = {}
            if key.secret:
                headers["x-cg-pro-api-key"] = key.secret
            response = await self._http.get(
                f"{_COINGECKO_BASE}/simple/price",
                params={
                    "ids": slug,
                    "vs_currencies": "usd",
                    "include_market_cap": "true",
                    "include_24hr_vol": "true",
                },
                headers=headers,
                timeout=httpx.Timeout(self._settings.hourly_monitor_http_timeout_s),
            )
            response.raise_for_status()
            payload = msgspec.json.decode(response.content)
            if not isinstance(payload, dict):
                return Decimal("0"), None, None
            coin = payload.get(slug, {})
            if not isinstance(coin, dict):
                coin = {}
            price = Decimal(str(coin.get("usd", 0)))
            volume = _decimal_or_none(coin.get("usd_24h_vol"))
            market_cap = _decimal_or_none(coin.get("usd_market_cap"))
            return price, volume, market_cap

        started = time.perf_counter()
        status = "failed"
        throttled = False
        try:
            result = await breaker.call(  # type: ignore[union-attr]
                lambda: fetch_with_backoff(
                    _do_fetch,
                    max_attempts=self._settings.hourly_monitor_retry_max_attempts,
                    base_delay_s=self._settings.hourly_monitor_retry_base_delay_s,
                    max_delay_s=self._settings.hourly_monitor_retry_max_delay_s,
                    label=f"coingecko:{asset_base}:{key.key_id}",
                    retry_on_rate_limit=False,
                ),
                is_critical=True,
            )
            if result is None:
                raise RuntimeError("circuit_open")
            price, volume, market_cap = result
            status = "ok" if price > 0 else "degraded"
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                throttled = True
            raise
        except Exception as exc:
            logger.warning(
                "monitoring_coingecko_fetch_failed | asset={} | key={} | err={}",
                asset_base,
                key.key_id,
                exc,
            )
            raise
        finally:
            latency_ms = (time.perf_counter() - started) * 1000.0
            await record_fetch_outcome(
                self._redis,  # type: ignore[arg-type]
                provider="coingecko",
                key_id=key.key_id,
                success=status == "ok",
                latency_ms=latency_ms,
                throttled=throttled,
                failover=failover,
            )

        return price, volume, market_cap, key.key_id, latency_ms, status


def build_coingecko_key_pool(settings: PolarisSettings) -> FailoverKeyPool:
    """Primary key first; secondary used only after primary is rate-limited."""
    primary = settings.coingecko_api_key.get_secret_value()
    secondary = settings.coingecko_api_key_2.get_secret_value()
    keys: list[ApiKeySlot] = []
    if primary:
        keys.append(ApiKeySlot(key_id="primary", secret=primary))
    if secondary:
        keys.append(ApiKeySlot(key_id="secondary", secret=secondary))
    if not keys:
        keys.append(ApiKeySlot(key_id="anonymous", secret=""))
    return FailoverKeyPool(keys)


def _decimal_or_none(raw: object) -> Decimal | None:
    if raw is None:
        return None
    try:
        return Decimal(str(raw))
    except Exception:
        return None
