"""
Async httpx client for Polymarket Gamma API and CLOB.

Section 5 Architecture: Macro Context — Capital-Weighted Probabilities.
Queries the Gamma API for market discovery and the CLOB (Central Limit
Order Book) for live pricing. Uses BBO midpoint — NOT last-trade price —
as the implied probability.

Sentinel Invariants:
  - httpx.AsyncClient (never requests)
  - msgspec for JSON decode (never stdlib json)
  - asyncio.to_thread for heavy deserialization
  - Explicit timeouts on every network call
  - CancelledError always re-raised
  - Loguru positional format (never f-strings / kwargs)
  - Max 40 lines per function
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

import httpx
import msgspec
from loguru import logger


# ──────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────

_DEFAULT_GAMMA_BASE: str = "https://gamma-api.polymarket.com"
_DEFAULT_CLOB_BASE: str = "https://clob.polymarket.com"
_DEFAULT_TIMEOUT: float = 10.0
_MAX_RESULTS_PER_PAGE: int = 100


class PolymarketClient:
    """
    Async wrapper for Polymarket Gamma + CLOB APIs.

    Section 5 Architecture: Macro Context — Capital-Weighted Probabilities.
    Provides market search and order book retrieval for BBO midpoint
    probability extraction.
    """

    def __init__(
        self,
        gamma_base_url: str = _DEFAULT_GAMMA_BASE,
        clob_base_url: str = _DEFAULT_CLOB_BASE,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        """
        Initialise the Polymarket client.

        Args:
            gamma_base_url: Gamma API root URL.
            clob_base_url: CLOB API root URL.
            timeout: HTTP request timeout in seconds.
        """
        self._gamma_base: str = gamma_base_url.rstrip("/")
        self._clob_base: str = clob_base_url.rstrip("/")
        self._timeout: float = timeout
        self._client: httpx.AsyncClient = httpx.AsyncClient(
            http2=True,
            timeout=httpx.Timeout(timeout, connect=5.0),
            limits=httpx.Limits(
                max_connections=20,
                max_keepalive_connections=10,
                keepalive_expiry=30.0,
            ),
            headers={"User-Agent": "ATLAS-PredictionMarket/1.0"},
        )

    # ──────────────────────────────────────────────────────────
    # Market Search (Gamma API)
    # ──────────────────────────────────────────────────────────

    async def search_markets(
        self,
        query: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """
        Search Polymarket for markets matching a query string.

        Section 5 Architecture: Macro Context — Capital-Weighted Probabilities.

        Args:
            query: Search term (e.g. "SEC", "Fed rate").
            limit: Maximum number of results.

        Returns:
            List of market dicts from the Gamma API.
        """
        results: list[dict[str, Any]] = []
        offset: int = 0
        remaining: int = min(limit, 200)

        while remaining > 0:
            page_size: int = min(remaining, _MAX_RESULTS_PER_PAGE)
            page: list[dict[str, Any]] = await self._fetch_gamma_page(
                query, offset, page_size
            )
            if not page:
                break
            results.extend(page)
            offset += len(page)
            remaining -= len(page)
            if len(page) < page_size:
                break

        return results

    async def _fetch_gamma_page(
        self,
        query: str,
        offset: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        """
        Fetch a single page from the Gamma API.

        Args:
            query: Search string.
            offset: Pagination offset.
            limit: Page size.

        Returns:
            List of market dicts for this page.
        """
        url: str = f"{self._gamma_base}/markets"
        params: dict[str, Any] = {
            "closed": "false",
            "limit": limit,
            "offset": offset,
        }
        if query:
            params["tag"] = query

        return await self._get_json_list(url, params)

    # ──────────────────────────────────────────────────────────
    # Order Book (CLOB API)
    # ──────────────────────────────────────────────────────────

    async def fetch_order_book(
        self,
        token_id: str,
    ) -> dict[str, Any]:
        """
        Fetch the live order book for a YES token from the CLOB.

        Section 5 Architecture: Macro Context — Capital-Weighted Probabilities.
        Returns raw bids and asks for BBO midpoint calculation.

        Args:
            token_id: Polymarket condition token ID.

        Returns:
            Dict with 'bids' and 'asks' arrays.
        """
        url: str = f"{self._clob_base}/book"
        params: dict[str, str] = {"token_id": token_id}
        return await self._get_json_dict(url, params)

    async def fetch_market_by_id(
        self,
        condition_id: str,
    ) -> dict[str, Any]:
        """
        Fetch a single market by its condition ID.

        Args:
            condition_id: Polymarket condition ID.

        Returns:
            Market metadata dict.
        """
        url: str = f"{self._gamma_base}/markets/{condition_id}"
        return await self._get_json_dict(url, {})

    # ──────────────────────────────────────────────────────────
    # Internal HTTP helpers
    # ──────────────────────────────────────────────────────────

    async def _get_json_list(
        self,
        url: str,
        params: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """
        GET request returning a JSON array.

        Args:
            url: Request URL.
            params: Query parameters.

        Returns:
            Parsed JSON list.
        """
        raw_bytes: bytes = await self._safe_get(url, params)
        if not raw_bytes:
            return []
        decoded: Any = await asyncio.to_thread(
            msgspec.json.decode, raw_bytes
        )
        if isinstance(decoded, list):
            return decoded
        return []

    async def _get_json_dict(
        self,
        url: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """
        GET request returning a JSON object.

        Args:
            url: Request URL.
            params: Query parameters.

        Returns:
            Parsed JSON dict.
        """
        raw_bytes: bytes = await self._safe_get(url, params)
        if not raw_bytes:
            return {}
        decoded: Any = await asyncio.to_thread(
            msgspec.json.decode, raw_bytes
        )
        if isinstance(decoded, dict):
            return decoded
        return {}

    async def _safe_get(
        self,
        url: str,
        params: dict[str, Any],
    ) -> bytes:
        """
        Execute a GET with timeout and structured error handling.

        Args:
            url: Request URL.
            params: Query parameters.

        Returns:
            Raw response bytes, or empty bytes on error.
        """
        try:
            response: httpx.Response = await asyncio.wait_for(
                self._client.get(url, params=params),
                timeout=self._timeout,
            )
            response.raise_for_status()
            return response.content
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return self._handle_get_error(url, exc)

    def _handle_get_error(self, url: str, exc: Exception) -> bytes:
        """Log HTTP errors and return empty bytes."""
        if isinstance(exc, httpx.HTTPStatusError):
            logger.error(
                "Polymarket HTTP error | url={} | status={} | body={}",
                url, exc.response.status_code, exc.response.text[:200],
            )
        elif isinstance(exc, (httpx.TimeoutException, asyncio.TimeoutError)):
            logger.warning(
                "Polymarket timeout | url={} | timeout={}s",
                url, self._timeout,
            )
        else:
            logger.exception(
                "Polymarket unexpected error | url={} | error={}",
                url, exc,
            )
        return b""

    # ──────────────────────────────────────────────────────────
    # Lifecycle
    # ──────────────────────────────────────────────────────────

    async def close(self) -> None:
        """Close the underlying httpx client."""
        await self._client.aclose()
