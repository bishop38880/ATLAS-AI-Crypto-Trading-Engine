"""Tests for AgentCorrelationTracker.

Uses ``fakeredis.FakeAsyncRedis`` to avoid a running Redis instance.
"""

from __future__ import annotations

import numpy as np
import pytest
from fakeredis import FakeAsyncRedis

from atlas.ml.correlation_tracker import AgentCorrelationTracker


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def redis_client() -> FakeAsyncRedis:
    """Provide a fresh in-memory Redis instance."""
    return FakeAsyncRedis()


@pytest.fixture
def tracker(redis_client: FakeAsyncRedis) -> AgentCorrelationTracker:
    """Provide a tracker wired to fake Redis."""
    return AgentCorrelationTracker(redis_client)  # type: ignore[arg-type]


# ------------------------------------------------------------------
# Tests — store / retrieve
# ------------------------------------------------------------------


class TestStoreRetrieve:
    """Verify scores round-trip through Redis correctly."""

    @pytest.mark.asyncio
    async def test_record_and_retrieve_single_cycle(
        self, tracker: AgentCorrelationTracker
    ) -> None:
        scores = {"agent_a": 0.5, "agent_b": 0.8}
        await tracker.record_scores("cycle-1", scores)
        matrix = await tracker.compute_correlation_matrix()
        # Single cycle → identity matrix fallback
        assert matrix.shape == (2, 2)

    @pytest.mark.asyncio
    async def test_window_trims_beyond_200(
        self, tracker: AgentCorrelationTracker, redis_client: FakeAsyncRedis
    ) -> None:
        for i in range(210):
            await tracker.record_scores(
                f"cycle-{i}", {"a": float(i), "b": float(i * 2)}
            )
        count = await redis_client.zcard("correlation:scores:index")
        assert count == 200


# ------------------------------------------------------------------
# Tests — correlation detection
# ------------------------------------------------------------------


class TestCorrelation:
    """Verify correlation matrix and pair detection."""

    @pytest.mark.asyncio
    async def test_perfectly_correlated_agents(
        self, tracker: AgentCorrelationTracker
    ) -> None:
        """Two agents with identical signals → ρ ≈ 1.0."""
        for i in range(50):
            val = float(i) / 50.0
            await tracker.record_scores(
                f"cycle-{i}", {"agent_x": val, "agent_y": val}
            )
        pairs = await tracker.get_correlated_pairs(threshold=0.7)
        assert len(pairs) == 1
        name_a, name_b, rho = pairs[0]
        assert {name_a, name_b} == {"agent_x", "agent_y"}
        assert rho > 0.99

    @pytest.mark.asyncio
    async def test_independent_agents_low_rho(
        self, tracker: AgentCorrelationTracker
    ) -> None:
        """Agents with unrelated signals → ρ ≈ 0.0."""
        rng = np.random.default_rng(42)
        for i in range(100):
            await tracker.record_scores(
                f"cycle-{i}",
                {
                    "agent_a": float(rng.random()),
                    "agent_b": float(rng.random()),
                },
            )
        pairs = await tracker.get_correlated_pairs(threshold=0.7)
        assert len(pairs) == 0

    @pytest.mark.asyncio
    async def test_rho_values_are_native_float(
        self, tracker: AgentCorrelationTracker
    ) -> None:
        """Returned ρ values must be native Python ``float``."""
        for i in range(50):
            val = float(i)
            await tracker.record_scores(
                f"cycle-{i}", {"a": val, "b": val, "c": val}
            )
        pairs = await tracker.get_correlated_pairs(threshold=0.5)
        for _, _, rho in pairs:
            assert type(rho) is float

    @pytest.mark.asyncio
    async def test_correlation_matrix_shape(
        self, tracker: AgentCorrelationTracker
    ) -> None:
        """Matrix shape is (n_agents, n_agents)."""
        for i in range(20):
            await tracker.record_scores(
                f"c-{i}",
                {"a": float(i), "b": float(i * 2), "c": float(i * 3)},
            )
        matrix = await tracker.compute_correlation_matrix()
        assert matrix.shape == (3, 3)
