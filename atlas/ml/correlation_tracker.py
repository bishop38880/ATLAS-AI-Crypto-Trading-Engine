"""Agent Correlation Tracker — rolling window of agent scores in Redis.

Maintains the last 200 cycles of agent scores and computes a correlation
matrix via ``numpy.corrcoef``.  Heavy matrix ops are offloaded through
``asyncio.to_thread``.

Keys
----
- ``correlation:scores:index`` — sorted set, score = ordinal
- ``correlation:scores:data:{cycle_id}`` — msgspec-encoded agent scores
"""

from __future__ import annotations

import asyncio

import msgspec
import numpy as np
import redis.asyncio as redis_async
from loguru import logger

_WINDOW_SIZE: int = 200
_INDEX_KEY: str = "correlation:scores:index"
_DATA_PREFIX: str = "correlation:scores:data:"


class AgentCorrelationTracker:
    """Tracks agent scores across cycles and computes correlations."""

    def __init__(self, redis_client: redis_async.Redis) -> None:  # type: ignore[type-arg]
        """Initialise with a connected ``redis.asyncio.Redis`` client."""
        self._redis = redis_client
        self._cycle_ordinal: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def record_scores(
        self, cycle_id: str, scores: dict[str, float]
    ) -> None:
        """Store agent scores for one pipeline cycle.

        Args:
            cycle_id: Unique identifier for this scoring cycle.
            scores: Mapping of agent_name → normalised score.
        """
        self._cycle_ordinal += 1
        data = msgspec.json.encode(scores)

        pipe = self._redis.pipeline()
        pipe.zadd(_INDEX_KEY, {cycle_id: self._cycle_ordinal})
        pipe.set(f"{_DATA_PREFIX}{cycle_id}", data)
        await pipe.execute()

        await self._trim_window()

    async def compute_correlation_matrix(self) -> np.ndarray:
        """Return the agent-agent correlation matrix.

        Returns:
            ``np.ndarray`` of shape ``(n_agents, n_agents)``.
        """
        matrix, _ = await self._fetch_score_matrix()
        if matrix.shape[0] < 2:
            logger.warning("correlation_insufficient_data | n_cycles={}", matrix.shape[0])
            n = matrix.shape[1] if matrix.ndim == 2 else 0
            return np.eye(n)

        return await asyncio.to_thread(_compute_corrcoef, matrix)

    async def get_correlated_pairs(
        self, threshold: float = 0.7
    ) -> list[tuple[str, str, float]]:
        """Return agent pairs with |ρ| above *threshold*.

        Returns:
            List of ``(agent_a, agent_b, rho)`` tuples.
        """
        matrix, agent_names = await self._fetch_score_matrix()
        if matrix.shape[0] < 2:
            return []

        corr = await asyncio.to_thread(_compute_corrcoef, matrix)
        return _extract_pairs(corr, agent_names, threshold)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _trim_window(self) -> None:
        """Remove cycles beyond the rolling window from Redis."""
        count = await self._redis.zcard(_INDEX_KEY)
        if count <= _WINDOW_SIZE:
            return

        excess = count - _WINDOW_SIZE
        old_ids: list[bytes] = await self._redis.zrange(
            _INDEX_KEY, 0, excess - 1
        )
        pipe = self._redis.pipeline()
        for cid in old_ids:
            key = cid.decode() if isinstance(cid, bytes) else cid
            pipe.delete(f"{_DATA_PREFIX}{key}")
        pipe.zremrangebyrank(_INDEX_KEY, 0, excess - 1)
        await pipe.execute()

    async def _fetch_score_matrix(
        self,
    ) -> tuple[np.ndarray, list[str]]:
        """Retrieve all stored cycles and build a numpy matrix.

        Returns:
            Tuple of ``(score_matrix, agent_names)`` where
            ``score_matrix`` has shape ``(n_cycles, n_agents)``.
        """
        cycle_ids: list[bytes] = await self._redis.zrange(
            _INDEX_KEY, 0, -1
        )
        if not cycle_ids:
            return np.empty((0, 0)), []

        decoded_rows = await self._load_cycle_data(cycle_ids)
        if not decoded_rows:
            return np.empty((0, 0)), []

        agent_names = sorted(decoded_rows[0].keys())
        return _rows_to_matrix(decoded_rows, agent_names), agent_names

    async def _load_cycle_data(
        self, cycle_ids: list[bytes]
    ) -> list[dict[str, float]]:
        """Load and decode score dicts for the given cycle IDs."""
        pipe = self._redis.pipeline()
        for cid in cycle_ids:
            key = cid.decode() if isinstance(cid, bytes) else cid
            pipe.get(f"{_DATA_PREFIX}{key}")
        raw_values: list[bytes | None] = await pipe.execute()

        rows: list[dict[str, float]] = []
        for raw in raw_values:
            if raw is not None:
                rows.append(msgspec.json.decode(raw, type=dict[str, float]))
        return rows


# ------------------------------------------------------------------
# Pure functions (safe for asyncio.to_thread)
# ------------------------------------------------------------------


def _compute_corrcoef(matrix: np.ndarray) -> np.ndarray:
    """Compute Pearson correlation via ``numpy.corrcoef``."""
    return np.corrcoef(matrix, rowvar=False)  # type: ignore[return-value]


def _rows_to_matrix(
    rows: list[dict[str, float]], agent_names: list[str]
) -> np.ndarray:
    """Convert list of score dicts into a 2-D numpy array."""
    data = [
        [row.get(name, 0.0) for name in agent_names]
        for row in rows
    ]
    return np.array(data)


def _extract_pairs(
    corr: np.ndarray,
    agent_names: list[str],
    threshold: float,
) -> list[tuple[str, str, float]]:
    """Walk the upper triangle and return pairs above threshold."""
    pairs: list[tuple[str, str, float]] = []
    n = len(agent_names)
    for i in range(n):
        for j in range(i + 1, n):
            rho = float(corr[i, j])
            if abs(rho) > threshold:
                pairs.append((agent_names[i], agent_names[j], rho))
    return pairs
