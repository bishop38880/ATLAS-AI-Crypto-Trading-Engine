"""Circuit breaker factory for Bitget execution client.

Provides named ``pybreaker.CircuitBreaker`` instances injected at
construction time.  Tests control breaker state by injecting a
factory that returns pre-configured or mock breakers.

Architecture note:
    One breaker per endpoint category (read / write).  On breaker
    open, the execution client returns a DEGRADED ``PlaceOrderResult``
    — it does NOT raise to the caller.
"""

from __future__ import annotations

from typing import Protocol

import pybreaker


class CircuitBreakerFactory(Protocol):
    """Protocol for circuit breaker creation — injectable in tests."""

    def create(
        self,
        name: str,
        fail_max: int = 5,
        reset_timeout: int = 30,
    ) -> pybreaker.CircuitBreaker: ...


class DefaultCircuitBreakerFactory:
    """Production circuit breaker factory using pybreaker.

    Creates named breakers with configurable failure thresholds.
    Default: 5 consecutive failures → open for 30 seconds.
    """

    def create(
        self,
        name: str,
        fail_max: int = 5,
        reset_timeout: int = 30,
    ) -> pybreaker.CircuitBreaker:
        """Create a named circuit breaker.

        Args:
            name: Human-readable breaker name for logging.
            fail_max: Consecutive failures before opening.
            reset_timeout: Seconds before half-open retry.

        Returns:
            Configured pybreaker.CircuitBreaker instance.
        """
        return pybreaker.CircuitBreaker(
            name=name,
            fail_max=fail_max,
            reset_timeout=reset_timeout,
        )
