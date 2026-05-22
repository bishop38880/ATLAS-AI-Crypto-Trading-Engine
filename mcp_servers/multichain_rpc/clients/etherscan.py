"""Etherscan async REST client for EVM transaction ingestion.

Section 27.1 Phase 3 Architecture: Graphs-of-Graphs (GoG) Contagion Tracking (GoG Super-Layer).

Fetches standard transactions (``txlist``), internal transactions
(``txlistinternal``), and ERC-20 token transfers (``tokentx``) via
the Etherscan REST API.  All requests pass through the Redis ZSET
sliding-window rate limiter to respect Etherscan's strict 5 req/sec
limit on free/standard tiers.

This module uses a singleton ``httpx.AsyncClient`` with HTTP/2 and
connection pooling — never creates a client per request.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
import msgspec
from loguru import logger

from mcp_servers.multichain_rpc.utils.rate_limiter import (
    RedisSlidingWindowLimiter,
)

# ─── Constants ───────────────────────────────────────────────────────────────

_BASE_URL = "https://api.etherscan.io/api"
_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
_LIMITS = httpx.Limits(
    max_connections=10,
    max_keepalive_connections=5,
    keepalive_expiry=30.0,
)
_IO_TIMEOUT_S: float = 10.0

# Wei-to-ETH divisor — Decimal precision is mandatory
_WEI_DIVISOR = Decimal("1000000000000000000")


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _to_decimal(value: Any, default: str = "0") -> Decimal:
    """Safely cast any value to ``Decimal``.

    Handles ``None``, ``float``, ``int``, and ``str`` inputs.
    Returns the *default* on failure rather than raising.
    """
    if value is None:
        return Decimal(default)
    try:
        result = Decimal(str(value))
        if result.is_nan() or result.is_infinite():
            return Decimal(default)
        return result
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def _wei_to_eth(wei_value: Any) -> Decimal:
    """Convert wei string/int to ETH using Decimal arithmetic."""
    return _to_decimal(wei_value) / _WEI_DIVISOR


# ─── Client ──────────────────────────────────────────────────────────────────


class EtherscanClient:
    """Async HTTP client for Etherscan REST API.

    Section 27.1 Phase 3 Architecture: Graphs-of-Graphs (GoG) Contagion Tracking.

    Provides three fetch methods corresponding to Etherscan's
    ``txlist``, ``txlistinternal``, and ``tokentx`` endpoints.
    All requests are rate-limited via the Redis ZSET limiter.

    Args:
        api_key: Etherscan API key.
        limiter: Sliding-window rate limiter instance.
    """

    def __init__(
        self,
        api_key: str,
        limiter: RedisSlidingWindowLimiter,
    ) -> None:
        self._api_key = api_key
        self._limiter = limiter
        self._client = httpx.AsyncClient(
            http2=True,
            timeout=_TIMEOUT,
            limits=_LIMITS,
            headers={"User-Agent": "ATLAS-MultiChainRPC/1.0"},
        )

    async def close(self) -> None:
        """Release pooled connections."""
        await self._client.aclose()

    # ─── Public fetch methods ────────────────────────────────────────────

    async def fetch_standard_txs(
        self,
        address: str,
        start_block: int = 0,
        end_block: int = 99999999,
        sort: str = "desc",
    ) -> list[dict[str, Any]]:
        """Fetch standard (external) transactions for an address.

        Args:
            address: Ethereum address (0x-prefixed).
            start_block: Starting block number.
            end_block: Ending block number.
            sort: Sort order ('asc' or 'desc').

        Returns:
            List of raw transaction dicts from Etherscan.
        """
        params = self._build_params(
            module="account",
            action="txlist",
            address=address,
            startblock=str(start_block),
            endblock=str(end_block),
            sort=sort,
        )
        return await self._fetch_with_limit(params, "txlist")

    async def fetch_internal_txs(
        self,
        address: str,
        start_block: int = 0,
        end_block: int = 99999999,
        sort: str = "desc",
    ) -> list[dict[str, Any]]:
        """Fetch internal transactions for an address.

        Critical for detecting proxy liquidations and contract-to-contract
        value transfers invisible on the standard transaction list.

        Args:
            address: Ethereum address.
            start_block: Starting block number.
            end_block: Ending block number.
            sort: Sort order.

        Returns:
            List of raw internal transaction dicts.
        """
        params = self._build_params(
            module="account",
            action="txlistinternal",
            address=address,
            startblock=str(start_block),
            endblock=str(end_block),
            sort=sort,
        )
        return await self._fetch_with_limit(params, "txlistinternal")

    async def fetch_erc20_transfers(
        self,
        address: str,
        start_block: int = 0,
        end_block: int = 99999999,
        sort: str = "desc",
    ) -> list[dict[str, Any]]:
        """Fetch ERC-20 token transfers for an address.

        Args:
            address: Ethereum address.
            start_block: Starting block number.
            end_block: Ending block number.
            sort: Sort order.

        Returns:
            List of raw ERC-20 transfer dicts.
        """
        params = self._build_params(
            module="account",
            action="tokentx",
            address=address,
            startblock=str(start_block),
            endblock=str(end_block),
            sort=sort,
        )
        return await self._fetch_with_limit(params, "tokentx")

    # ─── Internal methods ────────────────────────────────────────────────

    def _build_params(self, **kwargs: str) -> dict[str, str]:
        """Build Etherscan query parameters with API key."""
        kwargs["apikey"] = self._api_key
        return kwargs

    async def _fetch_with_limit(
        self,
        params: dict[str, str],
        action_label: str,
    ) -> list[dict[str, Any]]:
        """Execute a rate-limited Etherscan API request.

        Acquires a rate limiter slot, makes the HTTP request,
        and parses the response.
        """
        await self._limiter.acquire()
        try:
            return await self._execute_request(params, action_label)
        except asyncio.CancelledError:
            raise  # ALWAYS re-raise
        except httpx.HTTPStatusError as exc:
            logger.error(
                "Etherscan HTTP error | action={} | status={} | body={}",
                action_label, exc.response.status_code,
                exc.response.text[:200],
            )
            return []
        except (httpx.TimeoutException, asyncio.TimeoutError):
            logger.warning(
                "Etherscan timeout | action={}", action_label,
            )
            return []
        except Exception as exc:
            logger.exception(
                "Etherscan unexpected error | action={}", action_label,
            )
            return []

    async def _execute_request(
        self,
        params: dict[str, str],
        action_label: str,
    ) -> list[dict[str, Any]]:
        """Make the HTTP GET and parse the response."""
        response = await asyncio.wait_for(
            self._client.get(_BASE_URL, params=params),
            timeout=_IO_TIMEOUT_S,
        )
        response.raise_for_status()
        payload = msgspec.json.decode(response.content)
        return self._extract_result(payload, action_label)

    def _extract_result(
        self,
        payload: Any,
        action_label: str,
    ) -> list[dict[str, Any]]:
        """Extract the 'result' list from the Etherscan response."""
        if not isinstance(payload, dict):
            logger.warning(
                "Etherscan non-dict response | action={}", action_label,
            )
            return []
        status = payload.get("status", "0")
        result = payload.get("result", [])
        if status != "1" or not isinstance(result, list):
            message = payload.get("message", "Unknown error")
            logger.warning(
                "Etherscan API error | action={} | msg={}",
                action_label, message,
            )
            return []
        return result
