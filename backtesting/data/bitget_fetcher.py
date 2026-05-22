"""Fetch historical OHLCV and funding rates from Bitget public REST API."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

import httpx
import msgspec
from loguru import logger

from backtesting.data.constants import (
    POLARIS_UNIVERSE_ASSETS,
    SYMBOL_MAP,
    TIMEFRAME_TO_BITGET,
)
from backtesting.data.models import FundingRateBar, OHLCVBar

BITGET_BASE_URL: str = "https://api.bitget.com"
REQUEST_DELAY_S: float = 0.06
MAX_429_RETRIES: int = 3
BACKOFF_SECONDS: tuple[float, ...] = (1.0, 2.0, 4.0)
OHLCV_PAGE_LIMIT: int = 200


class BitgetAssetNotFoundError(Exception):
    """Raised when Bitget has no perpetual market for the asset."""

    def __init__(self, asset: str) -> None:
        self.asset = asset
        super().__init__(f"Bitget perpetual not found for asset={asset}")


class BitgetNoDataError(Exception):
    """Raised when Bitget returns an empty history for the requested window."""

    def __init__(self, asset: str, start_date: str, end_date: str) -> None:
        self.asset = asset
        self.start_date = start_date
        self.end_date = end_date
        super().__init__(
            f"No Bitget data for asset={asset} start={start_date} end={end_date}"
        )


class BitgetHistoricalFetcher:
    """Fetch historical OHLCV and funding rates from Bitget public REST API."""

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        base_url: str = BITGET_BASE_URL,
    ) -> None:
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=base_url,
            http2=True,
            timeout=httpx.Timeout(15.0, connect=5.0),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
        self._request_lock = asyncio.Lock()

    async def close(self) -> None:
        """Close the underlying HTTP client when owned by this fetcher."""
        if self._owns_client:
            await self._client.aclose()

    async def fetch_ohlcv(
        self,
        asset: str,
        timeframe: str,
        start_date: str,
        end_date: str,
    ) -> list[OHLCVBar]:
        """Fetch complete OHLCV history between dates with pagination."""
        bitget_symbol = _resolve_bitget_symbol(asset)
        granularity = _resolve_granularity(timeframe)
        start_ms = _date_to_ms(start_date)
        end_ms = _date_to_ms(end_date, end_of_day=True)
        cursor_ms = start_ms
        collected: list[OHLCVBar] = []

        while cursor_ms < end_ms:
            payload = await self._request_json(
                "/api/v2/mix/market/candles",
                {
                    "symbol": bitget_symbol,
                    "granularity": granularity,
                    "startTime": str(cursor_ms),
                    "endTime": str(end_ms),
                    "limit": str(OHLCV_PAGE_LIMIT),
                },
                asset=asset,
            )
            rows = _extract_rows(payload)
            if not rows:
                break
            page_bars = _parse_ohlcv_rows(asset, timeframe, rows)
            collected.extend(page_bars)
            last_ms = int(rows[-1][0])
            next_ms = last_ms + _timeframe_ms(timeframe)
            if next_ms <= cursor_ms:
                break
            cursor_ms = next_ms
            logger.info(
                "bitget_ohlcv_progress | asset={} | bars={} | cursor={}",
                asset,
                len(collected),
                cursor_ms,
            )

        if not collected:
            raise BitgetNoDataError(asset, start_date, end_date)
        return _dedupe_ohlcv_bars(collected)

    async def fetch_funding_rates(
        self,
        asset: str,
        max_pages: int = 50,
    ) -> list[FundingRateBar]:
        """Fetch funding rate history with automatic pagination."""
        bitget_symbol = _resolve_bitget_symbol(asset)
        collected: list[FundingRateBar] = []

        for page_no in range(1, max_pages + 1):
            payload = await self._request_json(
                "/api/v2/mix/market/history-fund-rate",
                {
                    "symbol": bitget_symbol,
                    "pageSize": "100",
                    "pageNo": str(page_no),
                },
                asset=asset,
            )
            rows = _extract_rows(payload)
            if not rows:
                break
            collected.extend(_parse_funding_rows(asset, rows))
            if len(rows) < 100:
                break

        return _dedupe_funding_bars(collected)

    async def fetch_all_assets(
        self,
        assets: list[str],
        timeframe: str = "1h",
        start_date: str = "2024-01-01",
        end_date: str | None = None,
        include_funding: bool = True,
    ) -> dict[str, list[OHLCVBar]]:
        """Batch-fetch OHLCV for multiple assets with bounded concurrency."""
        resolved_end = end_date or date.today().isoformat()
        semaphore = asyncio.Semaphore(3)
        results: dict[str, list[OHLCVBar]] = {}
        total = len(assets)

        async def _fetch_one(index: int, symbol: str) -> None:
            async with semaphore:
                logger.info(
                    "Fetching {} assets... [{}/{}: {}]",
                    total,
                    index,
                    total,
                    symbol,
                )
                ohlcv = await self.fetch_ohlcv(
                    symbol, timeframe, start_date, resolved_end
                )
                results[symbol] = ohlcv
                if include_funding:
                    await self.fetch_funding_rates(symbol)

        await asyncio.gather(*[
            _fetch_one(index, symbol) for index, symbol in enumerate(assets, start=1)
        ])
        return results

    async def _request_json(
        self,
        path: str,
        params: dict[str, str],
        *,
        asset: str,
    ) -> dict[str, Any]:
        """GET with rate limiting, 429 backoff, and Bitget error mapping."""
        for attempt in range(MAX_429_RETRIES + 1):
            async with self._request_lock:
                response = await self._client.get(path, params=params)
                await asyncio.sleep(REQUEST_DELAY_S)

            if response.status_code == 429 and attempt < MAX_429_RETRIES:
                delay = BACKOFF_SECONDS[attempt]
                logger.warning(
                    "bitget_rate_limited | asset={} | retry={} | delay={}s",
                    asset,
                    attempt + 1,
                    delay,
                )
                await asyncio.sleep(delay)
                continue

            if response.status_code == 404:
                raise BitgetAssetNotFoundError(asset)

            response.raise_for_status()
            decoded = msgspec.json.decode(response.content)
            if not isinstance(decoded, dict):
                raise BitgetNoDataError(asset, "", "")
            code = str(decoded.get("code", ""))
            if code == "40014" and attempt < MAX_429_RETRIES:
                delay = BACKOFF_SECONDS[attempt]
                await asyncio.sleep(delay)
                continue
            if code not in ("00000", "0", ""):
                message = str(decoded.get("msg", "unknown"))
                if "not exist" in message.lower() or "404" in message:
                    raise BitgetAssetNotFoundError(asset)
                raise BitgetNoDataError(asset, "", "")
            return decoded

        raise BitgetNoDataError(asset, "", "")


def default_universe_assets() -> list[str]:
    """Return the default 33-asset POLARIS backtest universe."""
    return list(POLARIS_UNIVERSE_ASSETS)


def _resolve_bitget_symbol(asset: str) -> str:
    normalized = asset.strip().upper()
    if normalized in SYMBOL_MAP:
        return SYMBOL_MAP[normalized]
    if normalized.endswith("_UMCBL"):
        return normalized
    return f"{normalized}_UMCBL"


def _resolve_granularity(timeframe: str) -> str:
    key = timeframe.lower()
    if key not in TIMEFRAME_TO_BITGET:
        raise ValueError(f"Unsupported timeframe={timeframe}")
    return TIMEFRAME_TO_BITGET[key]


def _date_to_ms(value: str, *, end_of_day: bool = False) -> int:
    parsed = datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    if end_of_day:
        parsed = parsed.replace(hour=23, minute=59, second=59)
    return int(parsed.timestamp() * 1000)


def _timeframe_ms(timeframe: str) -> int:
    mapping = {"1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}
    return mapping[timeframe.lower()]


def _extract_rows(payload: dict[str, Any]) -> list[list[Any]]:
    data = payload.get("data")
    if not isinstance(data, list):
        return []
    return [row for row in data if isinstance(row, list)]


def _parse_ohlcv_rows(
    asset: str,
    timeframe: str,
    rows: list[list[Any]],
) -> list[OHLCVBar]:
    bars: list[OHLCVBar] = []
    for row in rows:
        timestamp = _ms_to_iso(int(row[0]))
        volume = Decimal(str(row[5]))
        volume_usd = Decimal(str(row[6])) if len(row) > 6 else volume
        bars.append(OHLCVBar(
            asset=asset,
            timestamp_utc=timestamp,
            open=Decimal(str(row[1])),
            high=Decimal(str(row[2])),
            low=Decimal(str(row[3])),
            close=Decimal(str(row[4])),
            volume=volume,
            volume_usd=volume_usd,
            timeframe=timeframe,
        ))
    return bars


def _parse_funding_rows(
    asset: str,
    rows: list[dict[str, Any]] | list[list[Any]],
) -> list[FundingRateBar]:
    parsed: list[FundingRateBar] = []
    for row in rows:
        if isinstance(row, dict):
            rate = Decimal(str(row["fundingRate"]))
            timestamp = _ms_to_iso(int(row["fundingTime"]))
        else:
            rate = Decimal(str(row[1]))
            timestamp = _ms_to_iso(int(row[0]))
        annualised = rate * Decimal("3") * Decimal("365")
        parsed.append(FundingRateBar(
            asset=asset,
            timestamp_utc=timestamp,
            funding_rate=rate,
            funding_rate_annualised=annualised,
            open_interest_usd=None,
        ))
    return parsed


def _ms_to_iso(timestamp_ms: int) -> str:
    moment = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
    return moment.isoformat()


def _dedupe_ohlcv_bars(bars: list[OHLCVBar]) -> list[OHLCVBar]:
    unique: dict[str, OHLCVBar] = {bar.timestamp_utc: bar for bar in bars}
    return sorted(unique.values(), key=lambda bar: bar.timestamp_utc)


def _dedupe_funding_bars(bars: list[FundingRateBar]) -> list[FundingRateBar]:
    unique: dict[str, FundingRateBar] = {bar.timestamp_utc: bar for bar in bars}
    return sorted(unique.values(), key=lambda bar: bar.timestamp_utc)
