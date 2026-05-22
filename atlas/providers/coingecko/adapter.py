"""CoinGecko / GeckoTerminal provider adapter.

POLARIS v2.0 | Section 4.1 Tier 2, Trust Rank #6
Scoping constraints:
    - CoinGecko base: Validation Gate cross-price + universe metadata.
    - GeckoTerminal: OnChainAgent DeFi rotation context (pools).
    No exchange execution awareness.

Architecture invariants:
    - All HTTP I/O via httpx.AsyncClient; JSON via msgspec (never response.json()).
    - Optional ``COINGECKO_API_KEY`` → ``x-cg-pro-api-key`` header.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal, cast

import httpx
import msgspec
import redis.asyncio as redis_async
from loguru import logger
from pydantic import BaseModel, TypeAdapter

from atlas.providers.base import BaseProvider, ProviderHealth
from atlas.providers.coingecko.models import CommunityData
from atlas.shared.config import PolarisSettings

# ── Constants ────────────────────────────────────────────────────
COINGECKO_BASE_URL: str = "https://api.coingecko.com/api/v3"
GECKOTERM_BASE_URL: str = "https://api.geckoterminal.com/api/v2"
COINGECKO_PRICE_TTL: int = 60
GECKOTERM_POOL_TTL: int = 300
COINGECKO_COIN_DETAIL_TTL: int = 300
COINGECKO_CATEGORIES_TTL: int = 3600
COINGECKO_TICKERS_TTL: int = 3600
COMMUNITY_DATA_TTL: int = 21600  # 6 hours — fundamentals, not signal cadence
PRICE_DIVERGENCE_THRESHOLD: float = 0.005  # 0.5% — documented validation gate tolerance
SUPPLY_JUMP_ALERT_PCT: float = 0.5  # circulating step → unlock / dilution suppressor hint
HTTP_TIMEOUT_SECONDS: int = 10
_BITGET_VOLUME_SANITY_USD: Decimal = Decimal("1000000")


def _decode_json_object(body: bytes) -> dict[str, Any]:
    """Decode JSON object from HTTP response body."""
    decoded: Any = msgspec.json.decode(body)
    if not isinstance(decoded, dict):
        return {}
    return cast(dict[str, Any], decoded)


def _decimal_or_none(raw: Any) -> Decimal | None:
    if raw is None:
        return None
    try:
        return Decimal(str(raw))
    except Exception:
        return None


def _int_or_none(raw: Any) -> int | None:
    """Coerce primitive JSON integers or floats into ``int``, else ``None``."""
    if raw is None:
        return None
    try:
        return int(round(float(raw)))
    except Exception:
        return None


def _float_or_none(raw: Any) -> float | None:
    """Parse optional dimensionless floats from loosely typed JSON payloads."""
    if raw is None:
        return None
    try:
        return float(raw)
    except Exception:
        return None


# ── Pydantic Models ─────────────────────────────────────────────
class PriceReferenceData(BaseModel, frozen=True):
    """Cross-reference snapshot from CoinGecko /simple/price (Validation Gate)."""

    asset: str
    price_usd: str
    market_cap_usd: Decimal
    volume_24h: Decimal
    last_updated_utc: str
    status: Literal["healthy", "degraded"]


class DexPoolData(BaseModel, frozen=True):
    """Single DEX pool snapshot from GeckoTerminal (OnChainAgent DeFi context)."""

    pool_address: str
    dex_name: str
    network: str
    price_usd: str
    liquidity_usd: Decimal
    volume_24h: Decimal
    price_change_24h_pct: float
    last_updated_utc: str
    status: Literal["healthy", "degraded"]


class CoinMarketSnapshot(BaseModel, frozen=True):
    """CoinGecko /coins/{id} — rank, supply, sectors, images, contracts."""

    coin_id: str
    market_cap_rank: int | None
    circulating_supply: Decimal | None
    total_supply: Decimal | None
    max_supply: Decimal | None
    categories: tuple[str, ...]
    image_small_url: str
    image_large_url: str
    platforms: dict[str, str]
    circulating_supply_pct_change_vs_prior: float | None
    likely_unlock_or_dilution_event: bool
    last_updated_utc: str
    status: Literal["healthy", "degraded"]


class CoinCategoryItem(BaseModel, frozen=True):
    """Row from GET /coins/categories/list."""

    category_id: str
    name: str


class CoinExchangeLiquidityHint(BaseModel, frozen=True):
    """Aggregated Bitget 24h notional from GET /coins/{id}/tickers."""

    coin_id: str
    bitget_usd_volume_24h: Decimal | None
    passes_volume_sanity: bool
    last_updated_utc: str
    status: Literal["healthy", "degraded"]


# ── Adapter ──────────────────────────────────────────────────────
class CoinGeckoAdapter(BaseProvider):
    """CoinGecko + GeckoTerminal: prices, pools, universe metadata, ticker sanity."""

    def __init__(
        self,
        redis_client: redis_async.Redis,  # type: ignore[type-arg]
        *,
        config: PolarisSettings | None = None,
    ) -> None:
        super().__init__("coingecko", redis_client)
        self._cfg = config or PolarisSettings()  # type: ignore[call-arg]
        api_key = self._cfg.coingecko_api_key.get_secret_value().strip()
        headers: dict[str, str] = {}
        if api_key:
            headers["x-cg-pro-api-key"] = api_key
        self._cg_headers = headers
        self._cg_client = httpx.AsyncClient(
            base_url=COINGECKO_BASE_URL,
            timeout=HTTP_TIMEOUT_SECONDS,
            headers=headers,
        )
        self._gt_client = httpx.AsyncClient(
            base_url=GECKOTERM_BASE_URL,
            timeout=HTTP_TIMEOUT_SECONDS,
        )

    # ── Surface 1: /simple/price ─────────────────────────────────
    async def fetch_price_reference(self, asset: str) -> PriceReferenceData:
        cache_key = f"provider:coingecko:price:{asset}"
        cached = await self._redis.get(cache_key)
        if cached:
            try:
                data = msgspec.json.decode(cached)
                if isinstance(data, dict):
                    return PriceReferenceData.model_validate(data)
            except Exception as exc:
                logger.warning("coingecko_price_cache_corrupt | asset={} | err={}", asset, exc)

        try:
            async with self._semaphore:
                resp = await self._cg_client.get(
                    "/simple/price",
                    params={
                        "ids": asset,
                        "vs_currencies": "usd",
                        "include_market_cap": "true",
                        "include_24hr_vol": "true",
                        "include_last_updated_at": "true",
                    },
                )
                resp.raise_for_status()

            data = self._parse_price_response(asset, _decode_json_object(resp.content))
            await self._redis.setex(
                cache_key,
                COINGECKO_PRICE_TTL,
                msgspec.json.encode(data.model_dump(mode="json")),
            )
            self.mark_healthy()
            return data
        except Exception as exc:
            logger.error("coingecko price fetch failed | asset={} | {}", asset, exc)
            self.mark_degraded("Price fetch failed for {}: {}".format(asset, exc))
            return self._calculate_empty_price(asset)

    def _parse_price_response(self, asset: str, raw: dict[str, Any]) -> PriceReferenceData:
        coin = raw.get(asset, {})
        if not isinstance(coin, dict):
            coin = {}
        last_upd_src = coin.get("last_updated_at")
        last_updated_utc = datetime.now(timezone.utc).isoformat()
        if isinstance(last_upd_src, (int, float)):
            last_updated_utc = datetime.fromtimestamp(
                float(last_upd_src),
                tz=timezone.utc,
            ).isoformat()
        return PriceReferenceData(
            asset=asset,
            price_usd=str(Decimal(str(coin.get("usd", 0)))),
            market_cap_usd=Decimal(str(coin.get("usd_market_cap", 0))),
            volume_24h=Decimal(str(coin.get("usd_24h_vol", 0))),
            last_updated_utc=last_updated_utc,
            status="healthy",
        )

    def _calculate_empty_price(self, asset: str) -> PriceReferenceData:
        return PriceReferenceData(
            asset=asset,
            price_usd="0",
            market_cap_usd=Decimal("0"),
            volume_24h=Decimal("0"),
            last_updated_utc=datetime.now(timezone.utc).isoformat(),
            status="degraded",
        )

    # ── Surface 2: GeckoTerminal pools ───────────────────────────
    async def fetch_dex_pool_data(self, asset: str) -> list[DexPoolData]:
        cache_key = f"provider:geckoterm:dex:{asset}"
        cached = await self._redis.get(cache_key)
        if cached:
            try:
                return self._deserialize_pool_list(cached)
            except Exception as exc:
                logger.warning("geckoterm_pool_cache_corrupt | asset={} | err={}", asset, exc)

        try:
            network, address = asset.split("/", 1)
            async with self._semaphore:
                resp = await self._gt_client.get(
                    f"/networks/{network}/tokens/{address}/pools",
                )
                resp.raise_for_status()

            pools = self._parse_pool_response(_decode_json_object(resp.content), network)
            await self._redis.setex(
                cache_key,
                GECKOTERM_POOL_TTL,
                self._serialize_pool_list(pools),
            )
            self.mark_healthy()
            return pools
        except Exception as exc:
            logger.error("geckoterm pool fetch failed | asset={} | {}", asset, exc)
            self.mark_degraded("Pool fetch failed for {}: {}".format(asset, exc))
            return []

    def _parse_pool_response(self, raw: dict[str, Any], network: str) -> list[DexPoolData]:
        pools: list[DexPoolData] = []
        now_iso = datetime.now(timezone.utc).isoformat()
        for item in raw.get("data", []):
            if not isinstance(item, dict):
                continue
            attrs = item.get("attributes", {})
            if not isinstance(attrs, dict):
                attrs = {}
            vol_block = attrs.get("volume_usd", {})
            vol_h24 = 0
            if isinstance(vol_block, dict):
                vol_h24 = vol_block.get("h24", 0)
            pct_block = attrs.get("price_change_percentage", {})
            pct_h24 = 0.0
            if isinstance(pct_block, dict):
                pct_h24 = float(pct_block.get("h24", 0))
            dex_label = str(attrs.get("dex_id") or attrs.get("name") or "")
            pools.append(
                DexPoolData(
                    pool_address=str(attrs.get("address", "")),
                    dex_name=dex_label,
                    network=network,
                    price_usd=str(Decimal(str(attrs.get("base_token_price_usd", "0")))),
                    liquidity_usd=Decimal(str(attrs.get("reserve_in_usd", 0))),
                    volume_24h=Decimal(str(vol_h24)),
                    price_change_24h_pct=pct_h24,
                    last_updated_utc=now_iso,
                    status="healthy",
                ),
            )
        return pools

    @staticmethod
    def _serialize_pool_list(pools: list[DexPoolData]) -> bytes:
        payload = TypeAdapter(list[DexPoolData]).dump_python(pools, mode="json")
        return msgspec.json.encode(payload)

    @staticmethod
    def _deserialize_pool_list(raw: str | bytes) -> list[DexPoolData]:
        data = msgspec.json.decode(raw)
        if not isinstance(data, list):
            return []
        return [DexPoolData.model_validate(item) for item in data]

    # ── Surface 3: /coins/{id} (universe + dilution) ──────────────
    async def fetch_coin_market_snapshot(self, coin_id: str) -> CoinMarketSnapshot:
        cache_key = f"provider:coingecko:coin:{coin_id}"
        cached = await self._redis.get(cache_key)
        if cached:
            try:
                d = msgspec.json.decode(cached)
                if isinstance(d, dict):
                    return CoinMarketSnapshot.model_validate(d)
            except Exception as exc:
                logger.warning("coingecko_coin_cache_corrupt | id={} | err={}", coin_id, exc)

        try:
            async with self._semaphore:
                resp = await self._cg_client.get(
                    f"/coins/{coin_id}",
                    params={
                        "localization": "false",
                        "tickers": "false",
                        "market_data": "true",
                        "community_data": "false",
                        "developer_data": "false",
                        "sparkline": "false",
                    },
                )
                resp.raise_for_status()

            raw = _decode_json_object(resp.content)
            snap = self._parse_coin_detail(coin_id, raw)
            await self._redis.setex(
                cache_key,
                COINGECKO_COIN_DETAIL_TTL,
                msgspec.json.encode(snap.model_dump(mode="json")),
            )
            self.mark_healthy()
            return snap
        except Exception as exc:
            logger.error("coingecko coin detail failed | id={} | {}", coin_id, exc)
            self.mark_degraded("Coin detail failed for {}: {}".format(coin_id, exc))
            return self._empty_coin_snapshot(coin_id)

    def _parse_coin_detail(self, coin_id: str, raw: dict[str, Any]) -> CoinMarketSnapshot:
        now_iso = datetime.now(timezone.utc).isoformat()
        rank = raw.get("market_cap_rank")
        rank_i: int | None = None
        if rank is not None:
            try:
                rank_i = int(rank)
            except (TypeError, ValueError):
                rank_i = None

        mdata = raw.get("market_data", {})
        if not isinstance(mdata, dict):
            mdata = {}

        circ = _decimal_or_none(mdata.get("circulating_supply"))
        total = _decimal_or_none(mdata.get("total_supply"))
        max_s = _decimal_or_none(mdata.get("max_supply"))

        cats_raw = raw.get("categories", [])
        categories: tuple[str, ...] = ()
        if isinstance(cats_raw, list):
            categories = tuple(str(c) for c in cats_raw if str(c).strip())

        img = raw.get("image", {})
        small_u, large_u = "", ""
        if isinstance(img, dict):
            small_u = str(img.get("small", "") or "")
            large_u = str(img.get("large", "") or "")

        plat = raw.get("platforms", {})
        platforms: dict[str, str] = {}
        if isinstance(plat, dict):
            for k, v in plat.items():
                if v:
                    platforms[str(k)] = str(v)

        return CoinMarketSnapshot(
            coin_id=coin_id,
            market_cap_rank=rank_i,
            circulating_supply=circ,
            total_supply=total,
            max_supply=max_s,
            categories=categories,
            image_small_url=small_u,
            image_large_url=large_u,
            platforms=platforms,
            circulating_supply_pct_change_vs_prior=None,
            likely_unlock_or_dilution_event=False,
            last_updated_utc=now_iso,
            status="healthy",
        )

    async def _apply_supply_delta_tracking(
        self,
        coin_id: str,
        circ: Decimal | None,
    ) -> tuple[float | None, bool]:
        if circ is None:
            return None, False
        prev_key = "provider:coingecko:circulating_prev:{}".format(coin_id)
        prior_raw = await self._redis.get(prev_key)
        pct_change: float | None = None
        unlock = False
        if prior_raw:
            try:
                prior_s = (
                    prior_raw.decode("utf-8")
                    if isinstance(prior_raw, bytes)
                    else str(prior_raw)
                )
                prev_dec = Decimal(prior_s)
                if prev_dec > 0:
                    pct_change = float((circ - prev_dec) / prev_dec * 100)
                    if pct_change >= SUPPLY_JUMP_ALERT_PCT:
                        unlock = True
            except Exception:
                pass
        await self._redis.set(prev_key, str(circ))
        return pct_change, unlock

    async def fetch_coin_market_snapshot_tracked(self, coin_id: str) -> CoinMarketSnapshot:
        """Like ``fetch_coin_market_snapshot`` but fills supply delta from Redis history."""
        base = await self.fetch_coin_market_snapshot(coin_id)
        if base.status != "healthy":
            return base
        pct, unlock = await self._apply_supply_delta_tracking(
            coin_id,
            base.circulating_supply,
        )
        return base.model_copy(
            update={
                "circulating_supply_pct_change_vs_prior": pct,
                "likely_unlock_or_dilution_event": unlock,
            },
        )

    def _empty_coin_snapshot(self, coin_id: str) -> CoinMarketSnapshot:
        return CoinMarketSnapshot(
            coin_id=coin_id,
            market_cap_rank=None,
            circulating_supply=None,
            total_supply=None,
            max_supply=None,
            categories=(),
            image_small_url="",
            image_large_url="",
            platforms={},
            circulating_supply_pct_change_vs_prior=None,
            likely_unlock_or_dilution_event=False,
            last_updated_utc=datetime.now(timezone.utc).isoformat(),
            status="degraded",
        )

    # ── Surface 4: category directory ─────────────────────────────
    async def fetch_coin_categories_list(self) -> list[CoinCategoryItem]:
        cache_key = "provider:coingecko:categories:list"
        cached = await self._redis.get(cache_key)
        if cached:
            try:
                rows = msgspec.json.decode(cached)
                if isinstance(rows, list):
                    return [CoinCategoryItem.model_validate(r) for r in rows]
            except Exception as exc:
                logger.warning("coingecko_categories_cache_corrupt | err={}", exc)

        try:
            async with self._semaphore:
                resp = await self._cg_client.get("/coins/categories/list")
                resp.raise_for_status()
            raw = msgspec.json.decode(resp.content)
            items: list[CoinCategoryItem] = []
            if isinstance(raw, list):
                for row in raw:
                    if not isinstance(row, dict):
                        continue
                    cid = str(row.get("category_id", "") or row.get("id", ""))
                    name = str(row.get("name", ""))
                    if cid and name:
                        items.append(CoinCategoryItem(category_id=cid, name=name))
            await self._redis.setex(
                cache_key,
                COINGECKO_CATEGORIES_TTL,
                msgspec.json.encode(TypeAdapter(list[CoinCategoryItem]).dump_python(items, mode="json")),
            )
            self.mark_healthy()
            return items
        except Exception as exc:
            logger.error("coingecko categories list failed | {}", exc)
            self.mark_degraded(str(exc))
            return []

    # ── Surface 5: Bitget ticker sanity ─────────────────────────
    async def fetch_bitget_liquidity_hint(self, coin_id: str) -> CoinExchangeLiquidityHint:
        cache_key = f"provider:coingecko:tickers:{coin_id}"
        cached = await self._redis.get(cache_key)
        if cached:
            try:
                d = msgspec.json.decode(cached)
                if isinstance(d, dict):
                    return CoinExchangeLiquidityHint.model_validate(d)
            except Exception:
                pass

        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            async with self._semaphore:
                resp = await self._cg_client.get(f"/coins/{coin_id}/tickers")
                resp.raise_for_status()
            raw = _decode_json_object(resp.content)
            vol = self._aggregate_bitget_usd_volume(raw)
            passes = vol is not None and vol >= _BITGET_VOLUME_SANITY_USD
            hint = CoinExchangeLiquidityHint(
                coin_id=coin_id,
                bitget_usd_volume_24h=vol,
                passes_volume_sanity=passes,
                last_updated_utc=now_iso,
                status="healthy",
            )
            await self._redis.setex(
                cache_key,
                COINGECKO_TICKERS_TTL,
                msgspec.json.encode(hint.model_dump(mode="json")),
            )
            self.mark_healthy()
            return hint
        except Exception as exc:
            logger.error("coingecko tickers failed | id={} | {}", coin_id, exc)
            self.mark_degraded(str(exc))
            return CoinExchangeLiquidityHint(
                coin_id=coin_id,
                bitget_usd_volume_24h=None,
                passes_volume_sanity=False,
                last_updated_utc=now_iso,
                status="degraded",
            )

    # ── Surface 6: /coins/{id} community enrichment ─────────────────
    async def fetch_community_data(self, asset: str) -> CommunityData:
        """
        Fetch CoinGecko community engagement snapshot.

        Endpoint: /coins/{id}?community_data=true&developer_data=false
        TTL: 21600s (6 hours). Cache key: provider:coingecko:community:{asset}

        Used by: universe admission filter, daily snapshot job, fundamentals panel.
        NEVER used by confluence scoring or hot-path signal generation.
        """
        cache_key = "provider:coingecko:community:{}".format(asset)
        cached = await self._redis.get(cache_key)
        if cached:
            try:
                data = msgspec.json.decode(cached)
                if isinstance(data, dict):
                    return CommunityData.model_validate(data)
            except Exception as exc:
                logger.warning("coingecko_community_cache_corrupt | asset={} | err={}", asset, exc)

        try:
            async with self._semaphore:
                resp = await self._cg_client.get(
                    "/coins/{}".format(asset),
                    params={
                        "localization": "false",
                        "tickers": "false",
                        "market_data": "false",
                        "community_data": "true",
                        "developer_data": "false",
                        "sparkline": "false",
                    },
                )
                resp.raise_for_status()

            payload = self._parse_community_data(asset, _decode_json_object(resp.content))
            encoded = msgspec.json.encode(payload.model_dump(mode="json"))
            await self._redis.setex(cache_key, COMMUNITY_DATA_TTL, encoded)
            self.mark_healthy()
            return payload

        except Exception as exc:
            logger.error(
                "coingecko_community_fetch_failed | asset={} | err={}",
                asset,
                exc,
            )
            self.mark_degraded("Community snapshot failed for {}: {}".format(asset, exc))
            return self._empty_community_snapshot(asset)

    def _parse_community_data(self, asset: str, raw: dict[str, Any]) -> CommunityData:
        """Hydrate CommunityData from the ``community_data`` block on /coins responses."""
        now_utc = datetime.now(timezone.utc)
        blob = raw.get("community_data", {})
        if not isinstance(blob, dict):
            blob = {}
        top_symbol_raw = raw.get("symbol")
        resolved_asset_symbol = asset
        if isinstance(top_symbol_raw, str) and top_symbol_raw.strip():
            resolved_asset_symbol = top_symbol_raw.strip().upper()

        return CommunityData(
            asset_symbol=resolved_asset_symbol,
            twitter_followers=_int_or_none(blob.get("twitter_followers")),
            reddit_subscribers=_int_or_none(blob.get("reddit_subscribers")),
            reddit_average_posts_48h=_float_or_none(blob.get("reddit_average_posts_48h")),
            reddit_average_comments_48h=_float_or_none(blob.get("reddit_average_comments_48h")),
            reddit_accounts_active_48h=_float_or_none(blob.get("reddit_accounts_active_48h")),
            telegram_channel_user_count=_int_or_none(blob.get("telegram_channel_user_count")),
            last_updated_utc=now_utc,
            status="healthy",
        )

    @staticmethod
    def _empty_community_snapshot(asset: str) -> CommunityData:
        """Return a degraded empty shell when upstream calls fail (never cached)."""
        return CommunityData(
            asset_symbol=asset,
            twitter_followers=None,
            reddit_subscribers=None,
            reddit_average_posts_48h=None,
            reddit_average_comments_48h=None,
            reddit_accounts_active_48h=None,
            telegram_channel_user_count=None,
            last_updated_utc=datetime.now(timezone.utc),
            status="degraded",
        )

    def _aggregate_bitget_usd_volume(self, raw: dict[str, Any]) -> Decimal | None:
        """Sum CoinGecko ``converted_volume.usd`` for Bitget markets."""
        tickers = raw.get("tickers", [])
        if not isinstance(tickers, list):
            return None
        total = Decimal("0")
        for t in tickers:
            if not isinstance(t, dict):
                continue
            market = t.get("market", {})
            if not isinstance(market, dict):
                continue
            ident = str(market.get("identifier", "")).lower()
            name = str(market.get("name", "")).lower()
            if "bitget" not in ident and "bitget" not in name:
                continue
            cv = t.get("converted_volume", {})
            usd = None
            if isinstance(cv, dict):
                usd = _decimal_or_none(cv.get("usd"))
            if usd is not None:
                total += usd
        if total <= 0:
            return None
        return total

    async def get_health_status(self) -> ProviderHealth:
        """Ping CoinGecko /ping to determine health status."""
        try:
            async with self._semaphore:
                resp = await self._cg_client.get("/ping")
                resp.raise_for_status()
            self.mark_healthy()
        except Exception as exc:
            self.mark_degraded("Health ping failed: {}".format(exc))

        return ProviderHealth(
            name=self.provider_name,
            status=self.status,
            last_update=time.monotonic(),
            error=self._last_error,
        )

    async def close(self) -> None:
        """Release HTTP client resources for both surfaces."""
        await self._cg_client.aclose()
        await self._gt_client.aclose()
