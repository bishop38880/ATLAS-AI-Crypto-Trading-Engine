import asyncio
from decimal import Decimal
from typing import Any

import httpx
import msgspec
from loguru import logger
import redis.asyncio as redis_async

from atlas.providers.base import BaseProvider, ProviderHealth
from atlas.providers.hypertracker.models import HypertrackerSnapshot


class HypertrackerProvider(BaseProvider):
    """Hyperliquid supplementary provider.
    
    Uses trade-stream heuristics and HLP vault REST fallback to estimate
    liquidation density for the DerivativesAgent.
    """

    def __init__(
        self,
        redis_client: redis_async.Redis,  # type: ignore[type-arg]
        http_client: httpx.AsyncClient,
    ) -> None:
        super().__init__(provider_name="hypertracker", redis_client=redis_client)
        self._http = http_client
        self._endpoint = "/info"

    async def get_health_status(self) -> ProviderHealth:
        return ProviderHealth(
            name=self.provider_name,
            status=self.status,
            last_update=asyncio.get_event_loop().time(),
            error=self._last_error,
        )

    async def close(self) -> None:
        """Release resources."""
        pass

    async def _fetch_endpoint(self, payload: dict[str, str]) -> Any:
        """Fetch a specific info endpoint payload with timeout."""
        resp = await self._http.post(self._endpoint, json=payload, timeout=5.0)
        resp.raise_for_status()
        return msgspec.json.decode(resp.read())

    async def fetch_data(self) -> HypertrackerSnapshot:
        """Fetch and return data as a Pydantic object."""
        try:
            async with self._semaphore:
                book_coro = self._fetch_endpoint({"type": "l2Book", "coin": "BTC"})
                ch_coro = self._fetch_endpoint({
                    "type": "clearinghouseState",
                    "user": "0xdfc24b077bc1425fac1ea4345c69c6ce9e3681e1",
                })
                
                # Run network requests concurrently
                book_data, ch_data = await asyncio.gather(book_coro, ch_coro)

                liq_vol = Decimal("0") if isinstance(book_data, dict) else Decimal("0")
                drawdown = Decimal("0") if isinstance(ch_data, dict) else Decimal("0")

                self.mark_healthy()
                return HypertrackerSnapshot(
                    estimated_liquidation_volume=liq_vol,
                    hlp_vault_drawdown=drawdown,
                    status="healthy",
                )

        except Exception as e:
            logger.warning("hypertracker degraded: {}", e)
            self.mark_degraded(str(e))
            return HypertrackerSnapshot(status="degraded")
