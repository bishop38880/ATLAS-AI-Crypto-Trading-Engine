"""HeliusProvider — Solana on-chain exchange flow via Helius REST + webhooks."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import msgspec
import redis.asyncio as redis_async
from loguru import logger

from atlas.providers.base import BaseProvider, ProviderHealth
from atlas.providers.helius.flow_classification import classify_polled_transaction
from atlas.providers.helius.models import HeliusScoringSignals
from atlas.providers.helius.price_cache import fetch_sol_jup_prices_usd
from atlas.providers.helius.solana_addresses import (
    ALL_EXCHANGE_ADDRESSES,
    EXCHANGE_ADDRESSES,
    HELIUS_ENABLED_SYMBOLS,
    WHALE_TX_THRESHOLD_USD,
)
from atlas.services import solana_flow_tracker
from atlas.shared.config import PolarisSettings

_HELIUS_BASE_URL = "https://api.helius.xyz"


class HeliusProvider(BaseProvider):
    """Tier 2 Solana on-chain — exchange flow for SOL/JUP scoring."""

    def __init__(
        self,
        redis_client: redis_async.Redis,  # type: ignore[type-arg]
        settings: PolarisSettings,
        http_client: httpx.AsyncClient,
    ) -> None:
        super().__init__("helius", redis_client, max_concurrent=5)
        self._settings = settings
        self._http = http_client
        self._last_success: float = 0.0
        api_key = settings.helius_api_key.get_secret_value()
        self._api_key: str = api_key if api_key else ""
        if not self._api_key:
            logger.warning("helius_provider_init | status=OFFLINE | reason=missing_api_key")
            self._status = "OFFLINE"

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    async def get_health_status(self) -> ProviderHealth:
        return ProviderHealth(
            name=self._provider_name,
            status=self._status,
            last_update=self._last_success,
            error=self._last_error,
        )

    async def close(self) -> None:
        logger.info("helius_provider_close | provider=helius")

    async def _check_connectivity(self) -> tuple[bool, str | None]:
        if not self.is_configured:
            return False, "missing_api_key"
        try:
            response = await asyncio.wait_for(
                self._http.get(
                    f"{_HELIUS_BASE_URL}/v0/network/tps",
                    params={"api-key": self._api_key},
                ),
                timeout=5.0,
            )
            if response.status_code == 200:
                return True, None
            if response.status_code == 401:
                return False, "invalid_api_key"
            return False, f"http_{response.status_code}"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return False, str(exc)

    async def fetch_network_tps(self) -> float | None:
        if not self.is_configured:
            return None
        try:
            response = await asyncio.wait_for(
                self._http.get(
                    f"{_HELIUS_BASE_URL}/v0/network/tps",
                    params={"api-key": self._api_key},
                ),
                timeout=5.0,
            )
            response.raise_for_status()
            raw: Any = msgspec.json.decode(response.content)
            if isinstance(raw, dict):
                tps = raw.get("tps")
                if tps is not None:
                    self._last_success = time.monotonic()
                    self.mark_healthy()
                    return float(tps)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.mark_degraded(str(exc))
        return None

    async def fetch_address_transactions(
        self,
        address: str,
        limit: int = 50,
    ) -> list[dict[str, Any]] | None:
        if not self.is_configured:
            return None
        try:
            response = await asyncio.wait_for(
                self._http.get(
                    f"{_HELIUS_BASE_URL}/v0/addresses/{address}/transactions",
                    params={"api-key": self._api_key, "limit": limit},
                ),
                timeout=10.0,
            )
            response.raise_for_status()
            decoded: Any = msgspec.json.decode(response.content)
            if isinstance(decoded, list):
                self._last_success = time.monotonic()
                self.mark_healthy()
                return [item for item in decoded if isinstance(item, dict)]
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.mark_degraded(str(exc))
        return None

    async def poll_exchange_flows(self, symbol: str = "SOL") -> int:
        """Polling fallback — classify recent exchange address transactions."""
        if not self.is_configured:
            return 0
        symbol_upper = symbol.upper()
        if symbol_upper not in HELIUS_ENABLED_SYMBOLS:
            return 0

        sol_price, _ = await fetch_sol_jup_prices_usd(self._http)
        if sol_price is None:
            return 0

        recorded = 0
        for exchange, addresses in EXCHANGE_ADDRESSES.items():
            for address in addresses:
                txs = await self.fetch_address_transactions(address, limit=20)
                if txs is None:
                    continue
                for tx in txs:
                    event = classify_polled_transaction(
                        tx, address, exchange, sol_price, symbol_upper,
                    )
                    if event is None or event.amount_usd < WHALE_TX_THRESHOLD_USD:
                        continue
                    if await solana_flow_tracker.record_flow_event(event):
                        recorded += 1

        if recorded > 0:
            self._last_success = time.monotonic()
            self.mark_healthy()
        return recorded

    async def get_scoring_signals(self, symbol: str = "SOL") -> HeliusScoringSignals | None:
        signals = await solana_flow_tracker.get_signals(symbol)
        if signals is None:
            return None
        return HeliusScoringSignals(
            exchange_netflow_24h=signals.exchange_netflow_24h,
            whale_tx_count_24h=signals.whale_tx_count_24h,
            flow_direction=signals.flow_direction,
            exchange_netflow_1h=signals.exchange_netflow_1h,
            exchange_netflow_4h=signals.exchange_netflow_4h,
            largest_single_tx_24h=signals.largest_single_tx_24h,
        )

    async def create_exchange_flow_webhook(self, webhook_url: str) -> dict[str, Any] | None:
        if not self.is_configured:
            return None
        try:
            response = await asyncio.wait_for(
                self._http.post(
                    f"{_HELIUS_BASE_URL}/v0/webhooks",
                    params={"api-key": self._api_key},
                    content=msgspec.json.encode({
                        "webhookURL": webhook_url,
                        "transactionTypes": ["TRANSFER", "SWAP"],
                        "accountAddresses": list(ALL_EXCHANGE_ADDRESSES),
                        "webhookType": "enhanced",
                        "txnStatus": "success",
                    }),
                ),
                timeout=15.0,
            )
            response.raise_for_status()
            decoded: Any = msgspec.json.decode(response.content)
            if isinstance(decoded, dict):
                self.mark_healthy()
                return decoded
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.mark_degraded(str(exc))
        return None

    async def list_webhooks(self) -> list[dict[str, Any]] | None:
        if not self.is_configured:
            return None
        try:
            response = await asyncio.wait_for(
                self._http.get(
                    f"{_HELIUS_BASE_URL}/v0/webhooks",
                    params={"api-key": self._api_key},
                ),
                timeout=10.0,
            )
            response.raise_for_status()
            decoded: Any = msgspec.json.decode(response.content)
            if isinstance(decoded, list):
                return [item for item in decoded if isinstance(item, dict)]
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.mark_degraded(str(exc))
        return None

    async def delete_webhook(self, webhook_id: str) -> bool:
        if not self.is_configured:
            return False
        try:
            response = await asyncio.wait_for(
                self._http.delete(
                    f"{_HELIUS_BASE_URL}/v0/webhooks/{webhook_id}",
                    params={"api-key": self._api_key},
                ),
                timeout=10.0,
            )
            return response.status_code in (200, 204)
        except asyncio.CancelledError:
            raise
        except Exception:
            return False
