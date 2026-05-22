"""
Async httpx client for Tenderly Simulate API.

PROMETHEUS Execution Router — Pre-Execution Security Guardrail.
Sends raw EVM transaction payloads to Tenderly's private fork
for shadow execution. Returns simulation status, gas usage,
and exact asset_changes for Shadow Delta calculation.

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
import hashlib
from typing import Any

import httpx
import msgspec
from loguru import logger

from ..models import AssetChange, DataStatus, TenderlySimulationResult

_DEFAULT_BASE_URL: str = "https://api.tenderly.co"
_DEFAULT_TIMEOUT: float = 2.0


class TenderlyClient:
    """
    Async wrapper for the Tenderly Simulate API.

    PROMETHEUS Execution Router — Pre-Execution Security Guardrail.
    """

    def __init__(
        self,
        access_key: str,
        account_slug: str,
        project_slug: str,
        base_url: str = _DEFAULT_BASE_URL,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        """
        Initialise the Tenderly client.

        Args:
            access_key: Tenderly API access key.
            account_slug: Tenderly account slug.
            project_slug: Tenderly project slug.
            base_url: API base URL.
            timeout: HTTP timeout in seconds.
        """
        self._account_slug: str = account_slug
        self._project_slug: str = project_slug
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
            headers={
                "X-Access-Key": access_key,
                "Content-Type": "application/json",
                "User-Agent": "ATLAS-ThreatIntel/1.0",
            },
        )

    async def simulate_transaction(
        self,
        network_id: str,
        from_addr: str,
        to_addr: str,
        calldata: str,
        value_wei: str,
    ) -> TenderlySimulationResult:
        """
        Execute a shadow simulation on Tenderly.

        PROMETHEUS Execution Router — Pre-Execution Security Guardrail.

        Args:
            network_id: Tenderly network ID (e.g. "1").
            from_addr: Sender address.
            to_addr: Destination contract address.
            calldata: Hex-encoded calldata.
            value_wei: ETH value in wei (string).

        Returns:
            TenderlySimulationResult with success, gas, asset_changes.
        """
        payload = self._build_payload(
            network_id, from_addr, to_addr, calldata, value_wei,
        )
        raw_bytes = await self._post_simulate(payload)
        if not raw_bytes:
            return self._degraded_result("API returned empty")
        return self._parse_response(raw_bytes)

    def _build_payload(
        self, network_id: str, from_addr: str,
        to_addr: str, calldata: str, value_wei: str,
    ) -> dict[str, Any]:
        """Build Tenderly simulate request payload."""
        return {
            "network_id": network_id,
            "from": from_addr, "to": to_addr,
            "input": calldata, "value": value_wei,
            "save": False, "save_if_fails": False,
            "simulation_type": "full",
        }

    async def _post_simulate(self, payload: dict[str, Any]) -> bytes:
        """POST to the Tenderly simulate endpoint."""
        url = (
            f"{self._base_url}/api/v1/account/"
            f"{self._account_slug}/project/"
            f"{self._project_slug}/simulate"
        )
        encoded = msgspec.json.encode(payload)
        try:
            resp = await asyncio.wait_for(
                self._client.post(url, content=encoded),
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
                "Tenderly HTTP error | url={} | status={}",
                url, exc.response.status_code,
            )
        elif isinstance(exc, (httpx.TimeoutException, asyncio.TimeoutError)):
            logger.warning("Tenderly timeout | url={}", url)
        else:
            logger.exception("Tenderly error | url={} | err={}", url, exc)

    def _parse_response(self, raw_bytes: bytes) -> TenderlySimulationResult:
        """Parse Tenderly simulation response."""
        try:
            data: dict[str, Any] = msgspec.json.decode(raw_bytes)
            txn = data.get("transaction", {})
            changes = self._extract_asset_changes(txn)
            resp_hash = hashlib.sha256(raw_bytes).hexdigest()[:16]
            return TenderlySimulationResult(
                success=bool(txn.get("status", False)),
                gas_used=int(txn.get("gas_used", 0)),
                asset_changes=changes,
                error_message=str(txn.get("error_message", "")),
                raw_response_hash=resp_hash,
            )
        except Exception as exc:
            logger.error("Tenderly parse error | error={}", exc)
            return self._degraded_result(str(exc))

    def _extract_asset_changes(
        self, txn: dict[str, Any],
    ) -> list[AssetChange]:
        """Extract asset_changes from transaction info."""
        info = txn.get("transaction_info", {})
        raw = info.get("asset_changes", [])
        return [
            self._parse_change(c) for c in raw
            if isinstance(c, dict)
        ]

    def _parse_change(self, c: dict[str, Any]) -> AssetChange:
        """Parse a single asset change entry."""
        ti = c.get("token_info", {})
        return AssetChange(
            token_address=str(ti.get("contract_address", "")),
            token_name=str(ti.get("name", "")),
            token_symbol=str(ti.get("symbol", "")),
            token_decimals=int(ti.get("decimals", 18)),
            from_address=str(c.get("from", "")),
            to_address=str(c.get("to", "")),
            amount=str(c.get("raw_amount", "0")),
            change_type=str(c.get("type", "transfer")),
        )

    def _degraded_result(self, error: str) -> TenderlySimulationResult:
        """Build a degraded result on failure."""
        return TenderlySimulationResult(
            success=False, error_message=error,
            status=DataStatus.DEGRADED,
        )

    async def close(self) -> None:
        """Close the underlying httpx client."""
        await self._client.aclose()
