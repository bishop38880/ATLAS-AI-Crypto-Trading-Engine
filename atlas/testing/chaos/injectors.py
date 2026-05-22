"""Fault injectors for chaos experiments."""

import asyncio
from typing import Any
from unittest.mock import patch, AsyncMock, MagicMock

import httpx
import redis.asyncio as redis_async
from atlas.testing.chaos.framework import BaseInjector
from atlas.shared.http_pool import ProviderHttpPool
from atlas.agents.base import BaseAgent


class ProviderDownInjector(BaseInjector):
    """Mocks ProviderHttpPool.get to raise ConnectError."""
    def __init__(self, target_provider: str) -> None:
        self.target = target_provider
        self.patcher = None
        self.original_get = ProviderHttpPool.get

    async def _mock_get(self, pool_self: ProviderHttpPool, *args: Any, **kwargs: Any) -> httpx.Response:
        if pool_self.provider_name == self.target:
            raise httpx.ConnectError("Chaos injection: Provider Down")
        return await self.original_get(pool_self, *args, **kwargs)

    async def inject(self) -> None:
        self.patcher = patch.object(ProviderHttpPool, "get", new=self._mock_get)
        self.patcher.start()

    async def remove(self) -> None:
        if self.patcher:
            self.patcher.stop()


class HydraStreamDeathInjector(BaseInjector):
    """Mocks redis.asyncio.Redis.xread and PubSub to simulate stream death."""
    def __init__(self) -> None:
        self.patcher_xread = None
        self.patcher_pubsub = None

    async def _mock_xread(self, *args: Any, **kwargs: Any) -> list:
        await asyncio.sleep(0.1)
        return []  # Simulate empty stream

    async def _mock_get_message(self, *args: Any, **kwargs: Any) -> dict | None:
        await asyncio.sleep(0.1)
        return None  # Simulate no pubsub messages

    async def inject(self) -> None:
        self.patcher_xread = patch("redis.asyncio.Redis.xread", new=self._mock_xread)
        self.patcher_pubsub = patch("redis.asyncio.client.PubSub.get_message", new=self._mock_get_message)
        self.patcher_xread.start()
        self.patcher_pubsub.start()

    async def remove(self) -> None:
        if self.patcher_xread:
            self.patcher_xread.stop()
        if self.patcher_pubsub:
            self.patcher_pubsub.stop()


class LatencySpikeInjector(BaseInjector):
    """Adds a delay to a provider's HTTP calls."""
    def __init__(self, target_provider: str, delay_s: float) -> None:
        self.target = target_provider
        self.delay = delay_s
        self.patcher = None
        self.original_get = ProviderHttpPool.get

    async def _mock_get(self, pool_self: ProviderHttpPool, *args: Any, **kwargs: Any) -> httpx.Response:
        if pool_self.provider_name == self.target:
            await asyncio.sleep(self.delay)
        return await self.original_get(pool_self, *args, **kwargs)

    async def inject(self) -> None:
        self.patcher = patch.object(ProviderHttpPool, "get", new=self._mock_get)
        self.patcher.start()

    async def remove(self) -> None:
        if self.patcher:
            self.patcher.stop()


class CorruptDataInjector(BaseInjector):
    """Mocks ProviderHttpPool.get to return malformed payload."""
    def __init__(self, target_provider: str) -> None:
        self.target = target_provider
        self.patcher = None
        self.original_get = ProviderHttpPool.get

    async def _mock_get(self, pool_self: ProviderHttpPool, *args: Any, **kwargs: Any) -> httpx.Response:
        if pool_self.provider_name == self.target:
            resp = MagicMock(spec=httpx.Response)
            resp.status_code = 200
            resp.content = b'{"garbage": ]"'
            return resp
        return await self.original_get(pool_self, *args, **kwargs)

    async def inject(self) -> None:
        self.patcher = patch.object(ProviderHttpPool, "get", new=self._mock_get)
        self.patcher.start()

    async def remove(self) -> None:
        if self.patcher:
            self.patcher.stop()


class AgentTimeoutInjector(BaseInjector):
    """Forces an agent's score method to exceed the tier deadline."""
    def __init__(self, target_agent: str) -> None:
        self.target = target_agent
        self.patcher = None
        self.original_score = BaseAgent.score

    async def _mock_score(self, agent_self: BaseAgent, *args: Any, **kwargs: Any) -> Any:
        if agent_self.name == self.target:
            await asyncio.sleep(0.1)  # Exceeds 0.08 budget
        return await self.original_score(agent_self, *args, **kwargs)

    async def inject(self) -> None:
        self.patcher = patch("atlas.agents.base.BaseAgent.score", new=self._mock_score)
        self.patcher.start()

    async def remove(self) -> None:
        if self.patcher:
            self.patcher.stop()


class RedisDownInjector(BaseInjector):
    """Mocks redis.asyncio.Redis.execute_command to raise ConnectionError."""
    def __init__(self) -> None:
        self.patcher = None
        self.original_exec = redis_async.Redis.execute_command

    async def _mock_exec(self, *args: Any, **kwargs: Any) -> Any:
        raise redis_async.ConnectionError("Chaos injection: Redis Down")

    async def inject(self) -> None:
        self.patcher = patch("redis.asyncio.Redis.execute_command", new=self._mock_exec)
        self.patcher.start()

    async def remove(self) -> None:
        if self.patcher:
            self.patcher.stop()

class MultiInjector(BaseInjector):
    """Runs multiple injectors sequentially."""
    def __init__(self, injectors: list[BaseInjector]) -> None:
        self.injectors = injectors

    async def inject(self) -> None:
        for inj in self.injectors:
            await inj.inject()

    async def remove(self) -> None:
        for inj in reversed(self.injectors):
            await inj.remove()
