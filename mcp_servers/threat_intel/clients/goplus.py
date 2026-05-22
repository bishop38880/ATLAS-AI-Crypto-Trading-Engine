"""
Async httpx client for GoPlus Token Security API.

PROMETHEUS Execution Router — Pre-Execution Security Guardrail.
Statically analyses ERC-20 token contract bytecode for malicious
capabilities: honeypots, pausable transfers, blacklists, unlimited
minting, and hidden transfer taxes.

Sentinel Invariants:
  - httpx.AsyncClient (never requests)
  - msgspec for JSON decode (never stdlib json)
  - asyncio.wait_for on every network call
  - CancelledError always re-raised
  - Loguru structured kwargs (never f-strings)
  - Max 40 lines per function
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

import httpx
import msgspec
from loguru import logger

from ..models import (
    DataStatus,
    TokenSecurityFlags,
    TokenSecurityReport,
)

_DEFAULT_BASE_URL: str = "https://api.gopluslabs.io"
_DEFAULT_TIMEOUT: float = 2.0


class GoPlusClient:
    """
    Async wrapper for the GoPlus Token Security API.

    PROMETHEUS Execution Router — Pre-Execution Security Guardrail.
    """

    def __init__(
        self,
        base_url: str = _DEFAULT_BASE_URL,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        """
        Initialise the GoPlus client.

        Args:
            base_url: GoPlus API base URL.
            timeout: HTTP timeout in seconds.
        """
        self._base_url: str = base_url.rstrip("/")
        self._timeout: float = timeout
        self._client: httpx.AsyncClient = httpx.AsyncClient(
            http2=True,
            timeout=httpx.Timeout(timeout, connect=2.0),
            limits=httpx.Limits(
                max_connections=20,
                max_keepalive_connections=10,
                keepalive_expiry=30.0,
            ),
            headers={"User-Agent": "ATLAS-ThreatIntel/1.0"},
        )

    async def scan_token(
        self,
        chain_id: str,
        token_address: str,
    ) -> TokenSecurityReport:
        """
        Scan a token contract for security threats.

        PROMETHEUS Execution Router — Pre-Execution Security Guardrail.

        Args:
            chain_id: Numeric chain ID (e.g. "1" for Ethereum).
            token_address: Token contract address.

        Returns:
            TokenSecurityReport with parsed flags.
        """
        raw_bytes = await self._fetch_token_security(
            chain_id, token_address,
        )
        if not raw_bytes:
            return self._degraded_report(
                token_address, chain_id, "API returned empty",
            )
        return self._parse_response(
            raw_bytes, token_address, chain_id,
        )

    async def _fetch_token_security(
        self, chain_id: str, token_address: str,
    ) -> bytes:
        """GET the GoPlus token security endpoint."""
        url = (
            f"{self._base_url}/api/v1"
            f"/token_security/{chain_id}"
        )
        params = {"contract_addresses": token_address.lower()}
        try:
            resp = await asyncio.wait_for(
                self._client.get(url, params=params),
                timeout=self._timeout,
            )
            resp.raise_for_status()
            return resp.content
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._log_error(url, exc)
            return b""

    def _log_error(self, url: str, exc: Exception) -> None:
        """Log HTTP errors with structured Loguru."""
        if isinstance(exc, httpx.HTTPStatusError):
            logger.error(
                "GoPlus HTTP error | url={} | status={}",
                url, exc.response.status_code,
            )
        elif isinstance(exc, (httpx.TimeoutException, asyncio.TimeoutError)):
            logger.warning("GoPlus timeout | url={}", url)
        else:
            logger.exception("GoPlus error | url={} | err={}", url, exc)

    def _parse_response(
        self, raw_bytes: bytes,
        token_address: str, chain_id: str,
    ) -> TokenSecurityReport:
        """Parse GoPlus response into TokenSecurityReport."""
        try:
            data: dict[str, Any] = msgspec.json.decode(raw_bytes)
            result = data.get("result", {})
            addr_lower = token_address.lower()
            token_data = result.get(addr_lower, {})
            if not token_data:
                return self._degraded_report(
                    token_address, chain_id, "Token not found",
                )
            flags = self._parse_flags(token_data)
            summary = self._build_summary(flags)
            return TokenSecurityReport(
                token_address=token_address,
                chain_id=chain_id,
                flags=flags,
                risk_summary=summary,
            )
        except Exception as exc:
            logger.error("GoPlus parse error | error={}", exc)
            return self._degraded_report(
                token_address, chain_id, str(exc),
            )

    def _parse_flags(
        self, data: dict[str, Any],
    ) -> TokenSecurityFlags:
        """Parse GoPlus boolean flags and tax rates."""
        return TokenSecurityFlags(
            is_honeypot=self._to_bool(data.get("is_honeypot", "0")),
            cannot_sell_all=self._to_bool(
                data.get("cannot_sell_all", "0"),
            ),
            transfer_pausable=self._to_bool(
                data.get("transfer_pausable", "0"),
            ),
            is_blacklisted=self._to_bool(
                data.get("is_blacklisted", "0"),
            ),
            is_mintable=self._to_bool(
                data.get("is_mintable", "0"),
            ),
            buy_tax=self._to_tax(data.get("buy_tax", "0")),
            sell_tax=self._to_tax(data.get("sell_tax", "0")),
            is_open_source=self._to_bool(
                data.get("is_open_source", "0"),
            ),
            is_proxy=self._to_bool(data.get("is_proxy", "0")),
            owner_address=str(data.get("owner_address", "")),
        )

    def _build_summary(self, flags: TokenSecurityFlags) -> str:
        """Build a human-readable risk summary."""
        risks: list[str] = []
        if flags.is_honeypot:
            risks.append("HONEYPOT")
        if flags.cannot_sell_all:
            risks.append("SELL_RESTRICTED")
        if flags.transfer_pausable:
            risks.append("PAUSABLE")
        if flags.is_blacklisted:
            risks.append("BLACKLISTABLE")
        if flags.is_mintable:
            risks.append("MINTABLE")
        if flags.buy_tax > Decimal("0.10"):
            risks.append(f"HIGH_BUY_TAX({flags.buy_tax})")
        if flags.sell_tax > Decimal("0.10"):
            risks.append(f"HIGH_SELL_TAX({flags.sell_tax})")
        if not risks:
            return "No critical risks detected"
        return "CRITICAL: " + ", ".join(risks)

    @staticmethod
    def _to_bool(value: Any) -> bool:
        """Convert GoPlus '0'/'1' string to bool."""
        return str(value).strip() == "1"

    @staticmethod
    def _to_tax(value: Any) -> Decimal:
        """Convert GoPlus tax string to Decimal."""
        try:
            return Decimal(str(value).strip())
        except Exception as exc:
            logger.warning("GoPlus tax parse failed | value={} | error={}", value, exc)
            return Decimal("0")

    def _degraded_report(
        self, token_address: str, chain_id: str, error: str,
    ) -> TokenSecurityReport:
        """Build a degraded report on failure."""
        return TokenSecurityReport(
            token_address=token_address,
            chain_id=chain_id,
            flags=TokenSecurityFlags(),
            risk_summary=f"DEGRADED: {error}",
            status=DataStatus.DEGRADED,
        )

    async def close(self) -> None:
        """Close the underlying httpx client."""
        await self._client.aclose()
