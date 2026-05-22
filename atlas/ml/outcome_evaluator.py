"""Outcome Evaluator for Thompson Sampling feedback loop."""

from __future__ import annotations

from decimal import Decimal
import asyncpg  # type: ignore[import-untyped]
import msgspec
from loguru import logger
from redis.asyncio import Redis

from atlas.ml.thompson_sampling import ThompsonSampler


class OutcomeEvaluator:
    """Listens to post-trade learning updates to adjust agent weights."""

    def __init__(self, redis_client: Redis, asyncpg_pool: asyncpg.Pool) -> None:
        """Initialize the evaluator."""
        self._redis = redis_client
        self._pool = asyncpg_pool
        self._thompson = ThompsonSampler(redis_client)
        self._running = False

    async def listen(self) -> None:
        """Subscribe to outcome updates and evaluate them."""
        self._running = True
        pubsub = self._redis.pubsub()
        await pubsub.subscribe("atlas:learning_updates")
        logger.info("OutcomeEvaluator listening on atlas:learning_updates")

        try:
            async for message in pubsub.listen():
                if not self._running:
                    break
                if message["type"] != "message":
                    continue

                await self._process_message(message["data"], self._pool)
        finally:
            await pubsub.unsubscribe("atlas:learning_updates")
            await pubsub.close()

    async def stop(self) -> None:
        """Stop listening."""
        self._running = False

    async def _process_message(self, data: bytes, pool: asyncpg.Pool) -> None:
        """Process a single trade outcome message."""
        try:
            payload = msgspec.json.decode(data)
            signal_id = payload["signal_id"]
            pnl = Decimal(str(payload["pnl_pct"]))

            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT agent_breakdown FROM signal_history WHERE signal_id = $1",
                    signal_id,
                    timeout=5.0,
                )

            if not row:
                logger.warning("No signal history found | signal_id={}", signal_id)
                return

            await self._evaluate_agents(row["agent_breakdown"], pnl)
        except Exception as e:
            logger.error("Error processing outcome: {}", e)

    async def _evaluate_agents(self, breakdown_json: str | bytes, pnl: Decimal) -> None:
        """Determine correctness of each agent and update Thompson Sampler."""
        breakdown = msgspec.json.decode(breakdown_json)
        
        for agent_name, agent_data in breakdown.items():
            direction = str(agent_data.get("direction", "")).lower()
            if direction not in ("bullish", "bearish"):
                continue

            was_correct = (direction == "bullish" and pnl > Decimal("0")) or \
                          (direction == "bearish" and pnl < Decimal("0"))

            await self._thompson.update(agent_name, was_correct)
