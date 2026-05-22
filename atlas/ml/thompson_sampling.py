"""Thompson Sampling for agent weight optimisation."""

from __future__ import annotations

import asyncio
import time

import numpy as np
from loguru import logger
from redis.asyncio import Redis


class ThompsonSampler:
    """Maintains Beta distributions for agent accuracy tracking."""

    def __init__(self, redis_client: Redis) -> None:
        """Initialize with async Redis client."""
        self._redis = redis_client

    async def update(self, agent_name: str, was_correct: bool) -> None:
        """Atomically initialize and update Beta parameters for an agent."""
        key = f"thompson:{agent_name}"
        await self._redis.hsetnx(key, "alpha", "1.0")  # type: ignore[misc]
        await self._redis.hsetnx(key, "beta", "1.0")  # type: ignore[misc]

        field = "alpha" if was_correct else "beta"
        new_value = await self._redis.hincrbyfloat(key, field, 1.0)  # type: ignore[misc]
        logger.debug(
            "thompson_updated | agent={} | field={} | new_value={}",
            agent_name, field, new_value,
        )

    async def sample_weights(self, agent_names: list[str]) -> dict[str, float]:
        """Sample weights from Beta distributions for the given agents."""
        if not agent_names:
            return {}

        params = await self._fetch_parameters(agent_names)

        def _sample() -> dict[str, float]:
            return {
                ag: float(np.random.beta(a, b))
                for ag, (a, b) in params.items()
            }

        if len(agent_names) > 10:
            weights = await asyncio.to_thread(_sample)
        else:
            weights = _sample()

        return self._normalize(weights, agent_names)

    async def get_expected_weights(self, agent_names: list[str]) -> dict[str, float]:
        """Compute expected values (alpha / (alpha + beta)) for agents."""
        if not agent_names:
            return {}

        params = await self._fetch_parameters(agent_names)
        weights = {ag: float(a / (a + b)) for ag, (a, b) in params.items()}
        return self._normalize(weights, agent_names)

    async def apply_decay(self, agent_name: str, decay_factor: float = 0.995) -> None:
        """Atomically decay alpha and beta using a Lua script."""
        key = f"thompson:{agent_name}"
        lua = """
        local alpha = tonumber(redis.call('HGET', KEYS[1], 'alpha') or '1.0')
        local beta = tonumber(redis.call('HGET', KEYS[1], 'beta') or '1.0')
        alpha = math.max(1.0, alpha * tonumber(ARGV[1]))
        beta = math.max(1.0, beta * tonumber(ARGV[1]))
        redis.call(
            'HSET', KEYS[1], 'alpha', alpha, 'beta', beta, 'last_decay_at', ARGV[2]
        )
        return {tostring(alpha), tostring(beta)}
        """
        await self._redis.eval(lua, 1, key, str(decay_factor), str(time.time()))  # type: ignore[misc]

    async def _fetch_parameters(
        self, agent_names: list[str],
    ) -> dict[str, tuple[float, float]]:
        """Fetch alpha and beta for multiple agents."""
        params = {}
        for agent in agent_names:
            key = f"thompson:{agent}"
            data = await self._redis.hgetall(key)  # type: ignore[misc]
            alpha = float(data.get(b"alpha", 1.0))
            beta = float(data.get(b"beta", 1.0))
            params[agent] = (alpha, beta)
        return params

    def _normalize(
        self, weights: dict[str, float], agent_names: list[str],
    ) -> dict[str, float]:
        """Normalize weights to sum to 1.0."""
        total = sum(weights.values())
        if total > 0:
            return {k: v / total for k, v in weights.items()}
        return {k: 1.0 / len(agent_names) for k in agent_names}
