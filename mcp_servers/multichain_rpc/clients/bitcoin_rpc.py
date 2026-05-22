"""Bitcoin Core JSON-RPC async client for UTXO ingestion.

Section 27.1 Phase 3 Architecture: Graphs-of-Graphs (GoG) Contagion Tracking (GoG Super-Layer).

Interfaces with a ``bitcoind`` node via JSON-RPC to fetch raw
transactions (``getrawtransaction`` with ``verbose=true``), decode
UTXO ``vin``/``vout`` structures, and reconstruct capital flow.

All Satoshi-to-BTC conversions use ``Decimal`` arithmetic with the
canonical ``Decimal("100000000")`` divisor — ``float`` is banned.

This module uses a singleton ``httpx.AsyncClient`` with connection
pooling and the Redis ZSET sliding-window rate limiter.
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

_TIMEOUT = httpx.Timeout(15.0, connect=5.0)
_LIMITS = httpx.Limits(
    max_connections=5,
    max_keepalive_connections=3,
    keepalive_expiry=30.0,
)
_IO_TIMEOUT_S: float = 15.0

# Satoshi-to-BTC divisor — exact Decimal, never float
_SATOSHI_DIVISOR = Decimal("100000000")


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _to_decimal(value: Any, default: str = "0") -> Decimal:
    """Safely cast any value to ``Decimal``."""
    if value is None:
        return Decimal(default)
    try:
        result = Decimal(str(value))
        if result.is_nan() or result.is_infinite():
            return Decimal(default)
        return result
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def _satoshi_to_btc(satoshi_value: Any) -> Decimal:
    """Convert satoshis to BTC using Decimal arithmetic."""
    return _to_decimal(satoshi_value) / _SATOSHI_DIVISOR


# ─── Client ──────────────────────────────────────────────────────────────────


class BitcoinRpcClient:
    """Async HTTP client for Bitcoin Core JSON-RPC.

    Section 27.1 Phase 3 Architecture: Graphs-of-Graphs (GoG) Contagion Tracking.

    Wraps ``getrawtransaction``, ``getblock``, and ``getblockcount``
    RPC methods.  All requests pass through the Redis ZSET rate
    limiter to prevent I/O overload on the node.

    Args:
        rpc_url: Bitcoin Core RPC endpoint URL.
        rpc_user: RPC authentication username.
        rpc_pass: RPC authentication password.
        limiter: Sliding-window rate limiter instance.
    """

    def __init__(
        self,
        rpc_url: str,
        rpc_user: str,
        rpc_pass: str,
        limiter: RedisSlidingWindowLimiter,
    ) -> None:
        self._rpc_url = rpc_url
        self._rpc_user = rpc_user
        self._rpc_pass = rpc_pass
        self._limiter = limiter
        self._request_id = 0
        self._client = httpx.AsyncClient(
            http2=False,  # Bitcoin Core RPC is HTTP/1.1
            timeout=_TIMEOUT,
            limits=_LIMITS,
            auth=(rpc_user, rpc_pass),
            headers={"Content-Type": "application/json"},
        )

    async def close(self) -> None:
        """Release pooled connections."""
        await self._client.aclose()

    # ─── Public RPC methods ──────────────────────────────────────────────

    async def fetch_raw_transaction(
        self,
        txid: str,
        verbose: bool = True,
    ) -> dict[str, Any]:
        """Fetch a decoded raw transaction by txid.

        Args:
            txid: Transaction ID (hex string).
            verbose: If True, returns decoded JSON (default).

        Returns:
            Decoded transaction dict, or empty dict on error.
        """
        return await self._rpc_call(
            "getrawtransaction",
            [txid, verbose],
            f"getrawtransaction:{txid[:16]}",
        )

    async def fetch_block(
        self,
        blockhash: str,
        verbosity: int = 1,
    ) -> dict[str, Any]:
        """Fetch block data by hash.

        Args:
            blockhash: Block hash (hex string).
            verbosity: 0=hex, 1=decoded, 2=decoded+tx.

        Returns:
            Block data dict, or empty dict on error.
        """
        return await self._rpc_call(
            "getblock",
            [blockhash, verbosity],
            f"getblock:{blockhash[:16]}",
        )

    async def fetch_block_count(self) -> int:
        """Get the current block height.

        Returns:
            Current block count, or 0 on error.
        """
        result = await self._rpc_call(
            "getblockcount",
            [],
            "getblockcount",
        )
        if isinstance(result, (int, float)):
            return int(result)
        return 0

    # ─── Internal RPC execution ──────────────────────────────────────────

    async def _rpc_call(
        self,
        method: str,
        params: list[Any],
        label: str,
    ) -> Any:
        """Execute a rate-limited Bitcoin Core JSON-RPC call.

        Args:
            method: RPC method name.
            params: RPC parameters list.
            label: Label for logging.

        Returns:
            The 'result' field from the RPC response.
        """
        await self._limiter.acquire()
        try:
            return await self._execute_rpc(method, params, label)
        except asyncio.CancelledError:
            raise  # ALWAYS re-raise
        except httpx.HTTPStatusError as exc:
            logger.error(
                "Bitcoin RPC HTTP error | method={} | status={}",
                label, exc.response.status_code,
            )
            return {}
        except (httpx.TimeoutException, asyncio.TimeoutError):
            logger.warning(
                "Bitcoin RPC timeout | method={}", label,
            )
            return {}
        except Exception as exc:
            logger.exception(
                "Bitcoin RPC unexpected error | method={}", label,
            )
            return {}

    async def _execute_rpc(
        self,
        method: str,
        params: list[Any],
        label: str,
    ) -> Any:
        """Build and send the JSON-RPC payload."""
        self._request_id += 1
        payload = self._build_rpc_payload(method, params)
        response = await asyncio.wait_for(
            self._client.post(self._rpc_url, content=payload),
            timeout=_IO_TIMEOUT_S,
        )
        response.raise_for_status()
        return self._parse_rpc_response(response.content, label)

    def _build_rpc_payload(
        self,
        method: str,
        params: list[Any],
    ) -> bytes:
        """Construct the JSON-RPC 1.0 request body via msgspec."""
        body = {
            "jsonrpc": "1.0",
            "id": self._request_id,
            "method": method,
            "params": params,
        }
        return msgspec.json.encode(body)

    def _parse_rpc_response(
        self,
        content: bytes,
        label: str,
    ) -> Any:
        """Parse the JSON-RPC response and extract 'result'."""
        decoded = msgspec.json.decode(content)
        if not isinstance(decoded, dict):
            logger.warning(
                "Bitcoin RPC non-dict response | method={}", label,
            )
            return {}
        error = decoded.get("error")
        if error is not None:
            logger.error(
                "Bitcoin RPC error | method={} | code={} | msg={}",
                label,
                error.get("code", "?"),
                error.get("message", "?"),
            )
            return {}
        return decoded.get("result", {})
