"""Ordered API key failover — primary until exhausted, then next key."""

from __future__ import annotations

from dataclasses import dataclass

from loguru import logger


@dataclass(frozen=True)
class ApiKeySlot:
    """One credential slot in a provider key pool (list order = priority)."""

    key_id: str
    secret: str


# Back-compat alias used by coingecko source builder.
WeightedApiKey = ApiKeySlot


class FailoverKeyPool:
    """Use keys in registration order; skip keys in rate-limit cooldown."""

    def __init__(self, keys: list[ApiKeySlot]) -> None:
        if not keys:
            raise ValueError("failover_key_pool_requires_at_least_one_key")
        self._keys = keys
        self._cooldown: dict[str, float] = {}

    @property
    def key_ids(self) -> tuple[str, ...]:
        return tuple(key.key_id for key in self._keys)

    def mark_cooldown(self, key_id: str, until_monotonic: float) -> None:
        """Mark a key exhausted until ``until_monotonic`` (``time.monotonic()`` clock)."""
        self._cooldown[key_id] = until_monotonic
        logger.warning(
            "monitoring_key_exhausted | key_id={} | until={}",
            key_id,
            until_monotonic,
        )

    def eligible_keys(self, now_monotonic: float) -> list[ApiKeySlot]:
        """Return keys not in cooldown, highest priority first."""
        eligible = [
            key
            for key in self._keys
            if self._cooldown.get(key.key_id, 0.0) <= now_monotonic
        ]
        if eligible:
            return eligible
        return list(self._keys)

    def select_key(self, now_monotonic: float) -> ApiKeySlot:
        """Return the highest-priority key that is not in cooldown."""
        return self.eligible_keys(now_monotonic)[0]


# Back-compat alias for imports/tests.
WeightedKeyPool = FailoverKeyPool
