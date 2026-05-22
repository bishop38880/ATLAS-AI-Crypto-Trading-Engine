"""Tests for BaseProvider ABC.

Tests live alongside code — never in a top-level tests/ directory.
"""

import asyncio

import pytest
import redis.asyncio as redis_async
from unittest.mock import AsyncMock

from atlas.providers.base import BaseProvider, ProviderHealth


# ---------------------------------------------------------------------------
# Concrete stub for testing the abstract base class
# ---------------------------------------------------------------------------


class StubProvider(BaseProvider):
    """Minimal concrete provider for testing BaseProvider behaviour."""

    async def get_health_status(self) -> ProviderHealth:
        """Return health snapshot using base class state.

        Returns:
            ProviderHealth reflecting current status.
        """
        return ProviderHealth(
            name=self._provider_name,
            status=self._status,
            last_update=0.0,
            error=self._last_error,
        )

    async def close(self) -> None:
        """No-op close for the stub.

        Returns:
            None
        """
        pass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_redis() -> AsyncMock:
    """Create a mock async Redis client.

    Returns:
        AsyncMock standing in for redis.asyncio.Redis.
    """
    return AsyncMock(spec=redis_async.Redis)


@pytest.fixture
def provider(mock_redis: AsyncMock) -> StubProvider:
    """Create a StubProvider with default settings.

    Args:
        mock_redis: Mocked Redis client.

    Returns:
        StubProvider instance with max_concurrent=10.
    """
    return StubProvider("test_provider", mock_redis, max_concurrent=10)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestProviderHealthModel:
    """Tests for the ProviderHealth frozen Pydantic model."""

    def test_health_model_creation(self) -> None:
        """ProviderHealth can be created with valid fields."""
        health = ProviderHealth(
            name="test",
            status="HEALTHY",
            last_update=100.0,
            error=None,
        )
        assert health.name == "test"
        assert health.status == "HEALTHY"
        assert health.last_update == 100.0
        assert health.error is None

    def test_health_model_frozen(self) -> None:
        """ProviderHealth instances are immutable."""
        health = ProviderHealth(
            name="test",
            status="HEALTHY",
            last_update=100.0,
        )
        with pytest.raises(Exception):
            health.name = "changed"  # type: ignore[misc]

    def test_health_model_with_error(self) -> None:
        """ProviderHealth can carry an error string."""
        health = ProviderHealth(
            name="failing",
            status="DEGRADED",
            last_update=50.0,
            error="connection timeout",
        )
        assert health.status == "DEGRADED"
        assert health.error == "connection timeout"


class TestBaseProviderInit:
    """Tests for BaseProvider constructor and properties."""

    def test_initial_state_is_healthy(
        self,
        provider: StubProvider,
    ) -> None:
        """Freshly created provider starts in HEALTHY state."""
        assert provider.status == "HEALTHY"
        assert provider.provider_name == "test_provider"

    def test_custom_provider_name(
        self,
        mock_redis: AsyncMock,
    ) -> None:
        """Provider name is stored correctly."""
        stub = StubProvider("custom_name", mock_redis)
        assert stub.provider_name == "custom_name"


class TestSemaphoreConcurrency:
    """Tests for the semaphore-based concurrency limiter."""

    @pytest.mark.asyncio
    async def test_semaphore_limits_concurrency(
        self,
        mock_redis: AsyncMock,
    ) -> None:
        """Semaphore limits concurrent access to max_concurrent."""
        max_concurrent = 2
        stub = StubProvider("sem_test", mock_redis, max_concurrent)
        entered_count = 0
        max_seen = 0
        barrier = asyncio.Event()

        async def guarded_work() -> None:
            """Simulate work within the semaphore."""
            nonlocal entered_count, max_seen
            async with stub._semaphore:
                entered_count += 1
                if entered_count > max_seen:
                    max_seen = entered_count
                await barrier.wait()
                entered_count -= 1

        tasks = [
            asyncio.create_task(guarded_work())
            for _ in range(5)
        ]
        await asyncio.sleep(0.05)
        barrier.set()
        await asyncio.gather(*tasks)

        assert max_seen <= max_concurrent


class TestStateTransitions:
    """Tests for mark_degraded / mark_healthy transitions."""

    def test_mark_degraded_sets_state(
        self,
        provider: StubProvider,
    ) -> None:
        """mark_degraded transitions to DEGRADED with error."""
        provider.mark_degraded("timeout")
        assert provider.status == "DEGRADED"
        assert provider._last_error == "timeout"

    def test_mark_healthy_clears_error(
        self,
        provider: StubProvider,
    ) -> None:
        """mark_healthy clears DEGRADED state and error."""
        provider.mark_degraded("timeout")
        provider.mark_healthy()
        assert provider.status == "HEALTHY"
        assert provider._last_error is None

    def test_mark_healthy_when_already_healthy(
        self,
        provider: StubProvider,
    ) -> None:
        """mark_healthy is idempotent when already HEALTHY."""
        provider.mark_healthy()
        assert provider.status == "HEALTHY"

    @pytest.mark.asyncio
    async def test_get_health_status_reflects_state(
        self,
        provider: StubProvider,
    ) -> None:
        """get_health_status returns current state correctly."""
        provider.mark_degraded("bad data")
        health = await provider.get_health_status()
        assert health.status == "DEGRADED"
        assert health.error == "bad data"

        provider.mark_healthy()
        health = await provider.get_health_status()
        assert health.status == "HEALTHY"
        assert health.error is None
