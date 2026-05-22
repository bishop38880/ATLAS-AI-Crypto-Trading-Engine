"""MEV Researcher MCP — tunable thresholds and TTLs."""

from __future__ import annotations

RING_MAX_EVENTS_PER_POOL: int = 512
EVENT_MAX_AGE_SECONDS: float = 15.0
PRUNE_INTERVAL_SECONDS: float = 3.0
ETH_FETCH_CONCURRENCY: int = 6
FLASHBOTS_CHUNK_BYTES: int = 4096


def ethereum_chain_normalized(name: str) -> str | None:
    """Return canonical ethereum chain label."""
    lowered: str = name.strip().lower()
    if lowered in ("ethereum", "eth", "evm"):
        return "ethereum"
    return None


def solana_chain_normalized(name: str) -> str | None:
    """Return canonical Solana chain label."""
    lowered: str = name.strip().lower()
    if lowered in ("solana", "sol", "svm"):
        return "solana"
    return None
