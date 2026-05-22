"""Exponential backoff reconnection manager for premium WebSocket feeds."""

from __future__ import annotations

import asyncio
import random

from loguru import logger


class ReconnectManager:
    """Manages reconnection attempts with exponential backoff and jitter."""

    def __init__(
        self,
        name: str,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        max_attempts: int | None = None,
    ) -> None:
        self.name = name
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.max_attempts = max_attempts
        self._attempt = 0
        self._running = True

    def reset(self) -> None:
        """Call after a successful connection run to reset backoff."""
        self._attempt = 0

    def stop(self) -> None:
        """Signal the reconnect loop to exit."""
        self._running = False

    async def attempts(self):
        """Async generator yielding attempt numbers with backoff between them."""
        while self._running:
            if self.max_attempts is not None and self._attempt >= self.max_attempts:
                logger.error(
                    "premium_reconnect_max_attempts | name={} | attempts={}",
                    self.name,
                    self._attempt,
                )
                break

            self._attempt += 1
            yield self._attempt

            if not self._running:
                break

            delay = min(self.base_delay * (2 ** (self._attempt - 1)), self.max_delay)
            jitter = delay * 0.1 * random.random()
            actual_delay = delay + jitter

            logger.info(
                "premium_reconnect_scheduled | name={} | delay_s={:.1f} | attempt={}",
                self.name,
                actual_delay,
                self._attempt,
            )
            await asyncio.sleep(actual_delay)
