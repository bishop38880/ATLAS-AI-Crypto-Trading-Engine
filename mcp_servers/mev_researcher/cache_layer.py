"""Thread-safe sliding-window cache keyed by surveillance targets."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque

from loguru import logger

from .config import DEFAULT_MAX_EVENTS_PER_POOL, DEFAULT_RING_BUFFER_SECONDS
from .models import PendingMempoolEvent


def canonical_evm_address(addr: str) -> str:
    """Lower-case 0x-prefixed EVM hex."""
    raw = addr.strip().lower()
    if raw.startswith("0x"):
        return raw
    return f"0x{raw}"


def canonical_sol_address(addr: str) -> str:
    """Whitespace-trimmed base58 pubkey string."""
    return addr.strip()


class ThreatCacheCoordinator:
    """
    Sliding-window mempool cache keyed by `(chain, pool_address)`.

    Section 25.5 Architecture: Protective Swarms and MEV-Aware Execution.
    """

    def __init__(
        self,
        max_age_seconds: float = DEFAULT_RING_BUFFER_SECONDS,
        max_events_per_pool: int = DEFAULT_MAX_EVENTS_PER_POOL,
    ) -> None:
        self._max_age_seconds = max_age_seconds
        self._max_events = max_events_per_pool
        self._lock = asyncio.Lock()
        self._watch_evm: set[str] = set()
        self._watch_sol: set[str] = set()
        self._ev_events: defaultdict[str, deque[PendingMempoolEvent]] = defaultdict(deque)
        self._sol_events: defaultdict[str, deque[PendingMempoolEvent]] = defaultdict(deque)

    async def manage_watchlist(
        self,
        chain: str,
        target_address: str,
        action: str,
    ) -> tuple[bool, str]:
        """Registers or clears a PROMETHEUS surveillance anchor."""
        act = action.strip().lower()
        chain_key = chain.strip().lower()
        if chain_key not in {"ethereum", "solana"}:
            return False, "unsupported_chain"

        if chain_key == "ethereum":
            normalized = canonical_evm_address(target_address)
            watch_bucket = self._watch_evm
            event_bucket = self._ev_events
        else:
            normalized = canonical_sol_address(target_address)
            watch_bucket = self._watch_sol
            event_bucket = self._sol_events

        async with self._lock:
            if act == "watch":
                watch_bucket.add(normalized)
                event_bucket[normalized]  # noqa: B018 reserve deque slot explicitly
                logger.info(
                    "mev_watchlist_add | chain={} | addr={}",
                    chain_key,
                    normalized,
                )
                return True, "watch_registered"

            if act == "unwatch":
                watch_bucket.discard(normalized)
                event_bucket.pop(normalized, None)
                logger.info(
                    "mev_watchlist_remove | chain={} | addr={}",
                    chain_key,
                    normalized,
                )
                return True, "watch_cleared"

            return False, "action_must_be_watch_or_unwatch"

    def _purge_stale_locked(
        self,
        store_events: defaultdict[str, deque[PendingMempoolEvent]],
    ) -> None:
        cutoff = time.time() - self._max_age_seconds
        for key, dq in store_events.items():
            while dq and dq[0].detected_at_unix < cutoff:
                dq.popleft()
            while len(dq) > self._max_events:
                dq.popleft()

        dead = [k for k, dq in store_events.items() if len(dq) == 0]
        for dk in dead:
            store_events.pop(dk, None)

    async def should_accept(self, chain: str, normalized_target: str) -> bool:
        """True when stream workers may retain events for PROMETHEUS."""
        ck = chain.strip().lower()
        async with self._lock:
            if ck == "ethereum":
                return normalized_target in self._watch_evm
            if ck == "solana":
                return normalized_target in self._watch_sol
            return False

    async def ingest_event(self, evt: PendingMempoolEvent) -> None:
        """Append to TTL ring-buffer when anchor remains watched."""
        store_events = (
            self._ev_events
            if evt.chain == "ethereum"
            else self._sol_events
        )
        watch_set = (
            self._watch_evm
            if evt.chain == "ethereum"
            else self._watch_sol
        )

        normalized = (
            canonical_evm_address(evt.pool_address)
            if evt.chain == "ethereum"
            else canonical_sol_address(evt.pool_address)
        )

        async with self._lock:
            if normalized not in watch_set:
                return

            self._purge_stale_locked(store_events)

            dq = store_events[normalized]
            dq.append(evt)
            while len(dq) > self._max_events:
                dq.popleft()

    async def watched_targets(self, chain: str) -> set[str]:
        """Enumerate active surveillance anchors (copy snapshot)."""
        ck = chain.strip().lower()
        async with self._lock:
            if ck == "ethereum":
                return set(self._watch_evm)
            if ck == "solana":
                return set(self._watch_sol)
            return set()

    async def snapshot_for_pool(self, chain: str, pool_address: str) -> list[PendingMempoolEvent]:
        """Read-only chronological copy for MCP tools."""
        ck = chain.strip().lower()
        if ck not in {"ethereum", "solana"}:
            logger.warning("snapshot_for_pool_unsupported_chain | chain={}", ck)
            return []
        normalized = (
            canonical_evm_address(pool_address)
            if ck == "ethereum"
            else canonical_sol_address(pool_address)
        )
        store = self._ev_events if ck == "ethereum" else self._sol_events

        async with self._lock:
            self._purge_stale_locked(store)
            dq = store.get(normalized)
            return list(dq) if dq else []
