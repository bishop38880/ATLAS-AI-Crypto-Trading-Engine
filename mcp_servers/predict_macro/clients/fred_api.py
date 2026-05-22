"""
Async FRED (Federal Reserve Economic Data) observations client.

MacroCrossMarketAgent - Fiat Gravity Engine: polling layer for DXY proxy,
Treasury yields, SOFR, and M2 with conservative request pacing.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import httpx
import msgspec
from loguru import logger

from ..config import (
    FRED_LOOKBACK_DAYS,
    FRED_OBSERVATIONS_URL,
    HTTP_TIMEOUT_SECONDS,
)


def _observation_start_iso() -> str:
    """Compute FRED observation_start as ISO calendar date."""
    start_day: date = date.today() - timedelta(days=FRED_LOOKBACK_DAYS)
    return start_day.isoformat()


def _decode_json(payload: bytes) -> dict[str, Any]:
    """Decode JSON bytes to dict via msgspec."""
    decoded: Any = msgspec.json.decode(payload)
    if not isinstance(decoded, dict):
        return {}
    return decoded


def _parse_observations(decoded: dict[str, Any]) -> list[tuple[date, Decimal]]:
    """Extract (observation_date, value) pairs from a FRED observations payload."""
    observations_raw: Any = decoded.get("observations", [])
    if not isinstance(observations_raw, list):
        return []

    parsed: list[tuple[date, Decimal]] = []
    for row in observations_raw:
        if not isinstance(row, dict):
            continue
        date_str: str = str(row.get("date", ""))
        value_raw: str = str(row.get("value", "."))
        if value_raw == ".":
            continue
        try:
            obs_date: date = date.fromisoformat(date_str)
            value_dec: Decimal = Decimal(value_raw)
        except (ValueError, ArithmeticError):
            continue
        parsed.append((obs_date, value_dec))

    parsed.sort(key=lambda item: item[0])
    return parsed


async def fetch_series_observations(
    client: httpx.AsyncClient,
    api_key: str,
    series_id: str,
) -> list[tuple[date, Decimal]]:
    """
    Fetch trailing observations for one FRED series.

    Returns empty list on failure (logged).
    """
    params: dict[str, str] = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "observation_start": _observation_start_iso(),
    }
    try:
        response: httpx.Response = await client.get(
            FRED_OBSERVATIONS_URL,
            params=params,
            timeout=HTTP_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        decoded: dict[str, Any] = _decode_json(response.content)
        return _parse_observations(decoded)

    except asyncio.CancelledError:
        raise
    except httpx.HTTPStatusError as exc:
        logger.error(
            "FRED HTTP error | series={} | status={} | body={}",
            series_id,
            exc.response.status_code,
            exc.response.text[:300],
        )
        return []
    except Exception as exc:
        logger.exception("FRED fetch failed | series={} | error={}", series_id, exc)
        return []


async def fetch_all_configured_series(
    client: httpx.AsyncClient,
    api_key: str,
    series_ids: tuple[str, ...],
) -> dict[str, list[tuple[date, Decimal]]]:
    """
    Fetch multiple FRED series sequentially to respect API rate limits.

    MacroCrossMarketAgent - Fiat Gravity Engine uses this for dual-frequency
    harmonisation against on-chain stablecoin mints.
    """
    results: dict[str, list[tuple[date, Decimal]]] = {}
    for series_id in series_ids:
        observations: list[tuple[date, Decimal]] = await fetch_series_observations(
            client, api_key, series_id
        )
        results[series_id] = observations
        await asyncio.sleep(0.35)
    return results


def utc_now() -> datetime:
    """Current UTC timestamp."""
    return datetime.now(tz=timezone.utc)
