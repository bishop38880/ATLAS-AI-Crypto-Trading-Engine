"""
Async httpx client for Kalshi REST API.

Section 5 Architecture: Macro Context — Capital-Weighted Probabilities.
Wraps the Kalshi v2 REST API for event/market discovery and order book
retrieval. Implements graceful degradation: if API credentials are
missing, the client reports DEGRADED status and returns empty results
(Polymarket-only fallback mode).

Kalshi pricing convention: yes_price and no_price are in **USD cents**
(0–100). This client normalises to 0.0–1.0 before returning data.

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
from typing import Any

import httpx
import msgspec
from loguru import logger


# ──────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────

_DEFAULT_BASE_URL: str = "https://api.elections.kalshi.com/trade-api/v2"
_DEFAULT_TIMEOUT: float = 10.0
_CENTS_DIVISOR: float = 100.0


class KalshiClient:
    """
    Async wrapper for Kalshi REST API v2.

    Section 5 Architecture: Macro Context — Capital-Weighted Probabilities.
    Degrades gracefully when credentials are absent — returns empty
    results with DEGRADED status, allowing Polymarket-only operation.
    """

    def __init__(
        self,
        base_url: str = _DEFAULT_BASE_URL,
        api_key: str = "",
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        """
        Initialise the Kalshi client.

        Args:
            base_url: Kalshi API root URL.
            api_key: Kalshi API key (empty = degraded mode).
            timeout: HTTP request timeout in seconds.
        """
        self._base_url: str = base_url.rstrip("/")
        self._api_key: str = api_key
        self._timeout: float = timeout
        self._available: bool = bool(api_key)

        headers: dict[str, str] = {
            "User-Agent": "ATLAS-PredictionMarket/1.0",
            "Accept": "application/json",
        }
        if self._available:
            headers["Authorization"] = f"Bearer {api_key}"

        self._client: httpx.AsyncClient = httpx.AsyncClient(
            http2=True,
            timeout=httpx.Timeout(timeout, connect=5.0),
            limits=httpx.Limits(
                max_connections=20,
                max_keepalive_connections=10,
                keepalive_expiry=30.0,
            ),
            headers=headers,
        )

    @property
    def is_available(self) -> bool:
        """Whether Kalshi credentials are configured."""
        return self._available

    # ──────────────────────────────────────────────────────────
    # Event / Market Search
    # ──────────────────────────────────────────────────────────

    async def search_events(
        self,
        query: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """
        Search Kalshi events matching a query.

        Section 5 Architecture: Macro Context — Capital-Weighted Probabilities.
        Returns empty list with log warning if credentials are absent.

        Args:
            query: Search term.
            limit: Maximum results.

        Returns:
            List of event dicts.
        """
        if not self._available:
            logger.warning(
                "Kalshi unavailable — no API key | query={}", query,
            )
            return []

        url: str = f"{self._base_url}/events"
        params: dict[str, Any] = {
            "status": "open",
            "limit": min(limit, 200),
        }
        if query:
            params["title"] = query

        return await self._get_events_list(url, params)

    async def _get_events_list(
        self,
        url: str,
        params: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """
        Parse events list from Kalshi response envelope.

        Args:
            url: Request URL.
            params: Query parameters.

        Returns:
            List of event dicts.
        """
        raw: dict[str, Any] = await self._get_json_dict(url, params)
        events: Any = raw.get("events", [])
        return events if isinstance(events, list) else []

    async def fetch_markets_for_event(
        self,
        event_ticker: str,
    ) -> list[dict[str, Any]]:
        """
        Fetch all markets under a Kalshi event.

        Args:
            event_ticker: Kalshi event ticker.

        Returns:
            List of market dicts.
        """
        if not self._available:
            return []

        url: str = f"{self._base_url}/markets"
        params: dict[str, str] = {"event_ticker": event_ticker}
        raw: dict[str, Any] = await self._get_json_dict(url, params)
        markets: Any = raw.get("markets", [])
        return markets if isinstance(markets, list) else []

    # ──────────────────────────────────────────────────────────
    # Order Book
    # ──────────────────────────────────────────────────────────

    async def fetch_order_book(
        self,
        ticker: str,
    ) -> dict[str, Any]:
        """
        Fetch the order book for a Kalshi market.

        Section 5 Architecture: Macro Context — Capital-Weighted Probabilities.

        Args:
            ticker: Kalshi market ticker.

        Returns:
            Dict with 'yes' and 'no' price levels.
        """
        if not self._available:
            return {}

        url: str = f"{self._base_url}/markets/{ticker}/orderbook"
        return await self._get_json_dict(url, {})

    # ──────────────────────────────────────────────────────────
    # Price normalisation
    # ──────────────────────────────────────────────────────────

    @staticmethod
    def normalise_cents_to_probability(cents: float) -> float:
        """
        Convert Kalshi cents (0–100) to normalised probability (0.0–1.0).

        Args:
            cents: Price in USD cents.

        Returns:
            Normalised probability float.
        """
        clamped: float = max(0.0, min(100.0, cents))
        return clamped / _CENTS_DIVISOR

    # ──────────────────────────────────────────────────────────
    # Internal HTTP helpers
    # ──────────────────────────────────────────────────────────

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
                "Kalshi HTTP error | url={} | status={} | body={}",
                url, exc.response.status_code, exc.response.text[:200],
            )
        elif isinstance(exc, (httpx.TimeoutException, asyncio.TimeoutError)):
            logger.warning(
                "Kalshi timeout | url={} | timeout={}s",
                url, self._timeout,
            )
        else:
            logger.exception(
                "Kalshi unexpected error | url={} | error={}",
                url, exc,
            )
        return b""

    # ──────────────────────────────────────────────────────────
    # Lifecycle
    # ──────────────────────────────────────────────────────────

    async def close(self) -> None:
        """Close the underlying httpx client."""
        await self._client.aclose()
