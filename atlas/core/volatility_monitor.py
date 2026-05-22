"""Volatility Monitor — adaptive TTL tracker for high/low volatility periods."""

import asyncio
from typing import Set
from loguru import logger
import redis.asyncio as redis_async

from atlas.providers.hydra.listener import HydraStreamListener

class VolatilityMonitor:
    """Tracks real-time volatility ratio per asset from HYDRA buffer.
    
    Reads from local HYDRA cascade stream buffer (no HTTP calls).
    Stores ratio in Redis at `volatility:{asset}:ratio`.
    """

    def __init__(self, hydra: HydraStreamListener, redis_client: redis_async.Redis):
        """Initialize the VolatilityMonitor.

        Args:
            hydra: Sibling HYDRA stream listener containing event buffer.
            redis_client: Async Redis connection.
        """
        self._hydra = hydra
        self._redis = redis_client
        self._update_task: asyncio.Task[None] | None = None
        self._background_tasks: Set[asyncio.Task] = set()

    async def get_vol_ratio(self, asset: str) -> float:
        """Calculate and store the 1h/30d volatility ratio.

        Reads from HYDRA. No external HTTP.
        
        Args:
            asset: The trading pair symbol.

        Returns:
            The calculated volatility ratio. Defaults to 1.0.
        """
        event = self._hydra.get_latest_event(asset)
        
        ratio = 1.0
        if event is not None:
            v_1h = event.current_1h_volatility
            v_30d = event.average_30d_volatility
            if v_1h is not None and v_30d is not None and v_30d > 0:
                ratio = v_1h / v_30d

        try:
            await self._redis.set(f"volatility:{asset}:ratio", str(ratio))
        except Exception as e:
            logger.error("failed to set volatility ratio for {}: {}", asset, e)

        return ratio

    def get_adaptive_ttl(self, base_ttl: int, vol_ratio: float) -> int:
        """Calculate adaptive TTL based on volatility ratio.

        Args:
            base_ttl: The standard TTL in seconds.
            vol_ratio: The current volatility ratio.

        Returns:
            Adaptive TTL clamped between 0.25x (high vol) and 4.0x (low vol).
        """
        if vol_ratio <= 0:
            return base_ttl
            
        multiplier = 1.0 / vol_ratio
        multiplier = max(0.25, min(multiplier, 4.0))
        return max(1, int(base_ttl * multiplier))

    async def start(self) -> None:
        """Start the background 60s update loop."""
        if self._update_task is not None:
            return
        self._update_task = asyncio.create_task(self._update_loop())
        logger.info("VolatilityMonitor started")

    async def _update_loop(self) -> None:
        """Update volatility ratios for all tracked assets every 60s."""
        try:
            while True:
                await asyncio.sleep(60)
                for asset in self._hydra._buffer.keys():
                    await self.get_vol_ratio(asset)
        except asyncio.CancelledError:
            logger.info("VolatilityMonitor loop cancelled")
            raise

    async def close(self) -> None:
        """Cancel background loop and clean up."""
        if self._update_task is not None:
            self._update_task.cancel()
            try:
                await self._update_task
            except asyncio.CancelledError:
                if not self._update_task.cancelled():
                    raise
            self._update_task = None
            logger.info("VolatilityMonitor closed")
