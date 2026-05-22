"""DefiLlama async client — Layer 1 tokenomics data.

ATLAS Intelligence Layer — NewsMacroAgent Integration.

Fetches circulating supply, total supply, and price data from
DefiLlama's free ``/coins`` endpoint.  All values are converted
to ``Decimal`` immediately upon receipt to prevent precision loss.

This module uses a singleton ``httpx.AsyncClient`` with connection
pooling — never creates a client per request.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
import msgspec
from loguru import logger

from mcp_servers.token_unlocks.models import DefiLlamaSupply

# ─── DefiLlama Coin ID Mapping ──────────────────────────────────────────────
# Maps our 33-asset universe tickers to DefiLlama coin identifiers.
# Format: "chain:address" or "coingecko:id" for native tokens.
SYMBOL_TO_DEFILLAMA: dict[str, str] = {
    "BTC": "coingecko:bitcoin",
    "ETH": "coingecko:ethereum",
    "SOL": "coingecko:solana",
    "XRP": "coingecko:ripple",
    "ADA": "coingecko:cardano",
    "AVAX": "coingecko:avalanche-2",
    "DOT": "coingecko:polkadot",
    "LINK": "coingecko:chainlink",
    "MATIC": "coingecko:matic-network",
    "ATOM": "coingecko:cosmos",
    "NEAR": "coingecko:near",
    "ICP": "coingecko:internet-computer",
    "APT": "coingecko:aptos",
    "SUI": "coingecko:sui",
    "SEI": "coingecko:sei-network",
    "TIA": "coingecko:celestia",
    "INJ": "coingecko:injective-protocol",
    "DOGE": "coingecko:dogecoin",
    "LTC": "coingecko:litecoin",
    "BCH": "coingecko:bitcoin-cash",
    "FIL": "coingecko:filecoin",
    "ARB": "coingecko:arbitrum",
    "OP": "coingecko:optimism",
    "RENDER": "coingecko:render-token",
    "FET": "coingecko:fetch-ai",
    "ONDO": "coingecko:ondo-finance",
    "TAO": "coingecko:bittensor",
    "AAVE": "coingecko:aave",
    "MKR": "coingecko:maker",
    "UNI": "coingecko:uniswap",
    "LDO": "coingecko:lido-dao",
    "JUP": "coingecko:jupiter-exchange-solana",
    "XLM": "coingecko:stellar",
}

_BASE_URL = "https://coins.llama.fi"
_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
_LIMITS = httpx.Limits(
    max_connections=10,
    max_keepalive_connections=5,
    keepalive_expiry=30.0,
)


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


class DefiLlamaClient:
    """Async HTTP client for DefiLlama free tokenomics endpoints.

    ATLAS Intelligence Layer — NewsMacroAgent.

    Provides ``fetch_supply`` to retrieve circulating and total supply
    for any asset in the 33-asset universe.  Connection is pooled via
    a singleton ``httpx.AsyncClient`` — never create per-request.
    """

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            http2=True,
            timeout=_TIMEOUT,
            limits=_LIMITS,
            headers={"User-Agent": "ATLAS-TokenUnlocks/1.0"},
        )

    async def close(self) -> None:
        """Release pooled connections."""
        await self._client.aclose()

    async def fetch_supply(self, symbol: str) -> DefiLlamaSupply:
        """Fetch circulating and total supply for *symbol*.

        Calls the DefiLlama ``/coins`` endpoint and converts all
        financial values to ``Decimal`` before returning.

        Args:
            symbol: Uppercase ticker (e.g. ``"SOL"``).

        Returns:
            ``DefiLlamaSupply`` with ``status="OK"`` on success,
            or ``status="DEGRADED"`` if the fetch fails.
        """
        coin_id = SYMBOL_TO_DEFILLAMA.get(symbol.upper())
        if coin_id is None:
            logger.warning(
                "Symbol not in DefiLlama map | symbol={}",
                symbol,
            )
            return DefiLlamaSupply(
                symbol=symbol.upper(),
                status="DEGRADED",
            )
        return await self._fetch_coin_data(symbol.upper(), coin_id)

    async def _fetch_coin_data(
        self, symbol: str, coin_id: str,
    ) -> DefiLlamaSupply:
        """Internal fetch with timeout and error handling."""
        url = f"{_BASE_URL}/coins/{coin_id}"
        try:
            response = await asyncio.wait_for(
                self._client.get(url),
                timeout=10.0,
            )
            response.raise_for_status()
            raw = msgspec.json.decode(response.content)
            return self._parse_coin_response(symbol, coin_id, raw)
        except asyncio.CancelledError:
            raise  # ALWAYS re-raise
        except httpx.HTTPStatusError as exc:
            logger.error(
                "DefiLlama HTTP error | symbol={} | status={} | body={}",
                symbol, exc.response.status_code,
                exc.response.text[:200],
            )
            return DefiLlamaSupply(symbol=symbol, status="DEGRADED")
        except (httpx.TimeoutException, asyncio.TimeoutError):
            logger.warning(
                "DefiLlama timeout | symbol={}", symbol,
            )
            return DefiLlamaSupply(symbol=symbol, status="DEGRADED")
        except Exception as exc:
            logger.exception(
                "DefiLlama unexpected error | symbol={}", symbol,
            )
            return DefiLlamaSupply(symbol=symbol, status="DEGRADED")

    def _parse_coin_response(
        self, symbol: str, coin_id: str, raw: Any,
    ) -> DefiLlamaSupply:
        """Parse raw DefiLlama JSON into a typed ``DefiLlamaSupply``."""
        coins = raw.get("coins", {}) if isinstance(raw, dict) else {}
        coin_data = coins.get(coin_id, {})

        price = _to_decimal(coin_data.get("price"))
        circulating = _to_decimal(
            coin_data.get("circulating_supply",
                          coin_data.get("circulatingSupply")),
        )
        total = _to_decimal(
            coin_data.get("total_supply",
                          coin_data.get("totalSupply")),
        )

        # Calculate derived fields with Decimal arithmetic
        circ_ratio = Decimal("0")
        if total > Decimal("0"):
            circ_ratio = circulating / total

        market_cap = circulating * price

        now_utc = datetime.now(timezone.utc).isoformat()

        return DefiLlamaSupply(
            symbol=symbol,
            circulating_supply=circulating,
            total_supply=total,
            circulating_ratio=circ_ratio,
            price_usd=price,
            market_cap_usd=market_cap,
            fetched_at=now_utc,
            status="OK",
        )
