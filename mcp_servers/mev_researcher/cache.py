"""Thread-safe asyncio ring-buffer for targeted mempool observations."""

from __future__ import annotations

import asyncio
from collections import deque
from decimal import Decimal
from time import monotonic

from loguru import logger

from .config import EVENT_MAX_AGE_SECONDS, RING_MAX_EVENTS_PER_POOL
from .models import PendingMempoolEvent


def buffer_key(chain: str, pool_address: str) -> str:
    """Build cache key scoped by chain."""
    normalized_pool: str = pool_address.strip()
    key: str = f"{chain.lower()}::{normalized_pool.lower()}"
    return key


class PoolRingBuffers:
    """
    Sliding window cache keyed by chain+pool.

    Section 25.5 Architecture — Targeted Threat Cache (asyncio guarded).
    """

    def __init__(self, max_age_seconds: float = EVENT_MAX_AGE_SECONDS) -> None:
        self._locks: asyncio.Lock = asyncio.Lock()
        self._buffers: dict[str, deque[PendingMempoolEvent]] = {}
        self._last_prune_wall: float = monotonic()
        self._max_age_seconds: float = max_age_seconds

    async def record_event(self, event: PendingMempoolEvent) -> None:
        """Append an observation after pruning expired rows."""
        key: str = buffer_key(event.chain, event.pool_address)
        async with self._locks:
            dq: deque[PendingMempoolEvent] = self._buffers.setdefault(
                key,
                deque(maxlen=RING_MAX_EVENTS_PER_POOL),
            )
            self._purge_locked(key, dq, event.captured_at_unix_ms)
            dq.append(event)
            logger.debug(
                "MEV buffer ingest | chain={} | pool={} | txs={}",
                event.chain,
                event.pool_address,
                len(dq),
            )

    async def prune_all(self, now_unix_ms: int) -> None:
        """Periodic trim across buckets."""
        async with self._locks:
            wall: float = monotonic()
            if wall - self._last_prune_wall < 2.5:
                return
            self._last_prune_wall = wall

            stale_keys: list[str] = []
            for bucket_key, dq in list(self._buffers.items()):
                self._purge_locked(bucket_key, dq, now_unix_ms)
                if len(dq) == 0:
                    stale_keys.append(bucket_key)

            for stale in stale_keys:
                del self._buffers[stale]

    async def snapshot_for_pool(
        self,
        *,
        chain: str,
        pool_address: str,
        now_unix_ms: int,
    ) -> tuple[list[PendingMempoolEvent], bool]:
        """
        Return cloned events younger than TTL.

        degraded=True when surveillance never produced data AND feed offline.
        For simplicity degrade flag is surfaced by coordinator, not buffers.
        """
        key: str = buffer_key(chain, pool_address)
        async with self._locks:
            dq: deque[PendingMempoolEvent] | None = self._buffers.get(key)
            if not dq:
                return [], False

            self._purge_locked(key, dq, now_unix_ms)
            return list(dq), True

    def _purge_locked(
        self,
        key: str,
        dq: deque[PendingMempoolEvent],
        now_unix_ms: int,
    ) -> None:
        """Remove expired events (expects lock held)."""
        max_age_ms: int = int(self._max_age_seconds * 1000)
        while dq:
            oldest: PendingMempoolEvent = dq[0]
            if now_unix_ms - oldest.captured_at_unix_ms > max_age_ms:
                dq.popleft()
            else:
                break


ZERO_DEC: Decimal = Decimal("0")
