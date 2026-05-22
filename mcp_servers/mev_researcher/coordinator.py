"""Background coordination for watchlists and feed health."""

from __future__ import annotations

import asyncio
from decimal import Decimal

from loguru import logger

from .cache import PoolRingBuffers


class SurveillanceCoordinator:
    """
    Owns asyncio-safe watchlists and buffer handles.

    Section 25.5 Architecture: Targeted watchlist isolation.
    """

    def __init__(self, buffers: PoolRingBuffers | None = None) -> None:
        self.buffers: PoolRingBuffers = buffers or PoolRingBuffers()
        self._watch_lock: asyncio.Lock = asyncio.Lock()
        self._eth_watchlist: set[str] = set()
        self._sol_watchlist: set[str] = set()
        self.eth_feed_degraded: bool = True
        self.sol_feed_degraded: bool = True
        self.flashbots_degraded: bool = True

    async def ethereum_watch_addresses(self) -> set[str]:
        """Copy mutable watchlist snapshot."""
        async with self._watch_lock:
            return set(self._eth_watchlist)

    async def solana_watch_addresses(self) -> set[str]:
        """Copy mutable watchlist snapshot."""
        async with self._watch_lock:
            return set(self._sol_watchlist)

    async def add_watch(self, *, chain: str, address: str) -> None:
        """Normalize and insert address into watchlists."""

        chain_key: str = chain.strip().lower()

        async with self._watch_lock:
            if chain_key == "ethereum":
                ethereum_norm: str = address.strip().lower()
                self._eth_watchlist.add(ethereum_norm)

                logger.info("MEV surveillance watch ethereum | addr={}", ethereum_norm)

                return

            if chain_key == "solana":
                clipped_sol_pubkey: str = address.strip()

                self._sol_watchlist.add(clipped_sol_pubkey)

                logger.info("MEV surveillance watch solana | addr={}", clipped_sol_pubkey)

                return

        logger.warning("MEV surveillance unknown chain {} | skipping", chain)

    async def remove_watch(self, *, chain: str, address: str) -> None:
        """Remove watcher entry."""

        chain_key: str = chain.strip().lower()

        async with self._watch_lock:
            if chain_key == "ethereum":
                ethereum_norm = address.strip().lower()

                self._eth_watchlist.discard(ethereum_norm)

                return

            if chain_key == "solana":
                clipped_sol_pubkey = address.strip()

                self._sol_watchlist.discard(clipped_sol_pubkey)

    async def note_eth_health(self, *, degraded: bool) -> None:
        """Mark ethereum feed degraded state."""
        self.eth_feed_degraded = degraded

    async def note_sol_health(self, *, degraded: bool) -> None:
        """Mark Solana feed degraded state."""
        self.sol_feed_degraded = degraded

    async def note_flashbots_health(self, *, degraded: bool) -> None:
        """Mark Flashbots enrichment degraded state."""
        self.flashbots_degraded = degraded

    async def degraded_reason(
        self,
        *,
        chain: str,
    ) -> tuple[bool, str]:
        """Explain offline feeds for ToxicityReporting."""
        if chain == "ethereum":
            degraded: bool = self.eth_feed_degraded
            detail: str = "ethereum surveillance offline or misconfigured"

            async with self._watch_lock:
                empty_watch: bool = len(self._eth_watchlist) == 0

            if empty_watch:
                detail = detail + "; empty ethereum watchlist"
            return degraded, detail

        if chain == "solana":
            degraded_sol: bool = self.sol_feed_degraded
            detail_sol: str = "solana surveillance offline or misconfigured"

            async with self._watch_lock:
                empty_watch_sol: bool = len(self._sol_watchlist) == 0

            if empty_watch_sol:
                detail_sol = detail_sol + "; empty solana watchlist"

            return degraded_sol, detail_sol

        logger.warning("MEV degraded check unknown chain | chain={}", chain)
        return True, "unknown_chain"


QUOTE_ETH_USD_FALLBACK: Decimal = Decimal("3500")


def ethereum_price_quote(env_map: dict[str, str]) -> Decimal:
    """Resolve ETH quotation for rough USD notionals."""
    raw: str = env_map.get("ETH_QUOTE_USD") or ""

    try:
        if raw.strip():
            return Decimal(raw.strip())

    except ArithmeticError:
        logger.warning("Bad ETH_QUOTE_USD | using fallback={}", QUOTE_ETH_USD_FALLBACK)

    return QUOTE_ETH_USD_FALLBACK
