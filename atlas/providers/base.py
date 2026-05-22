"""BaseProvider — abstract base class for all ATLAS data providers.

Every external and internal data provider inherits from this class.
Provides: Redis client access, concurrency limiting via semaphore,
health status tracking (HEALTHY / DEGRADED / OFFLINE), and canonical
mark_degraded / mark_healthy state transitions.

Architecture note:
    This is the sanctioned provider-scaffolding pattern as of the
    audit remediation (Session 0). The previous ban on BaseProvider
    inheritance in 050-tech-stack.mdc has been lifted specifically
    because this session is the canonical origin of the class.
"""

from abc import ABC, abstractmethod
from typing import Literal

import asyncio

import redis.asyncio as redis_async
from loguru import logger
from pydantic import BaseModel


class ProviderHealth(BaseModel, frozen=True):
    """Immutable snapshot of provider health at a point in time.

    Attributes:
        name: Provider identifier (e.g. 'hydra', 'coinalyze').
        status: One of HEALTHY, DEGRADED, OFFLINE.
        last_update: Monotonic timestamp of last successful data receipt.
        error: Human-readable error string if degraded/offline, else None.
    """

    name: str
    status: Literal["HEALTHY", "DEGRADED", "OFFLINE"]
    last_update: float  # time.monotonic() timestamp
    error: str | None = None


class BaseProvider(ABC):
    """Abstract base class for all ATLAS data providers.

    Subclasses must implement:
        - get_health_status() -> ProviderHealth
        - close() -> None

    Args:
        provider_name: Unique identifier for this provider.
        redis_client: Shared async Redis connection.
        max_concurrent: Semaphore limit for concurrent operations.
    """

    def __init__(
        self,
        provider_name: str,
        redis_client: redis_async.Redis,  # type: ignore[type-arg]
        max_concurrent: int = 10,
    ) -> None:
        self._provider_name = provider_name
        self._redis = redis_client
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._status: Literal["HEALTHY", "DEGRADED", "OFFLINE"] = "HEALTHY"
        self._last_error: str | None = None

    @property
    def provider_name(self) -> str:
        """Return the provider's unique identifier."""
        return self._provider_name

    @property
    def status(self) -> Literal["HEALTHY", "DEGRADED", "OFFLINE"]:
        """Return the current provider status."""
        return self._status

    def mark_degraded(self, error: str) -> None:
        """Transition provider to DEGRADED state.

        Args:
            error: Human-readable description of the failure.
        """
        self._status = "DEGRADED"
        self._last_error = error
        logger.warning(
            "provider degraded | provider={} | error={}",
            self._provider_name,
            error,
        )

    def mark_healthy(self) -> None:
        """Transition provider back to HEALTHY state.

        Only logs recovery if the provider was previously degraded.
        """
        if self._status != "HEALTHY":
            logger.info(
                "provider recovered | provider={}",
                self._provider_name,
            )
        self._status = "HEALTHY"
        self._last_error = None

    @abstractmethod
    async def get_health_status(self) -> ProviderHealth:
        """Return an immutable health snapshot.

        Returns:
            ProviderHealth with current status, timestamp, and error.
        """
        ...

    @abstractmethod
    async def close(self) -> None:
        """Release resources held by this provider.

        Subclasses must cancel background tasks, close connections, etc.
        """
        ...
