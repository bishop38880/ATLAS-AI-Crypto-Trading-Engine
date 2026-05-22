"""Event-driven Multi-Asset Pipeline Runner."""

import asyncio
from typing import Any

import msgspec
import redis.asyncio as redis_async
from loguru import logger

from atlas.core.asset_universe import get_active_assets
from atlas.core.stream_consumer import StreamConsumer, StreamMessage
from atlas.models.signal import SignalDecision, SignalOutput
from atlas.signals.publisher import SignalPublisher


class MultiAssetRunner:
    """Event-driven loop for multiplexed HYDRA streams."""

    def __init__(
        self,
        redis_client: redis_async.Redis,  # type: ignore[type-arg]
        hydra_redis_client: redis_async.Redis | None = None,
    ) -> None:
        """Initialize the runner.

        Args:
            redis_client: Primary Redis (signal publish, app cache).
            hydra_redis_client: Redis where HYDRA writes ``atlas:stream:hydra:*``.
                Defaults to ``redis_client`` when omitted.
        """
        self._redis_signals = redis_client
        self._redis_streams = hydra_redis_client or redis_client
        self._consumer = StreamConsumer(self._redis_streams)
        self._publisher = SignalPublisher(redis_client)
        self._active_assets = get_active_assets()
        self._running = False
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the event-driven loop."""
        if self._running:
            return
        self._running = True

        await asyncio.gather(
            self._consumer.setup_group("atlas:stream:hydra:cascades", "scoring_group"),
            self._consumer.setup_group("atlas:stream:hydra:price", "scoring_group"),
            self._consumer.setup_group("atlas:stream:hydra:cascades", "rag_group"),
            self._consumer.setup_group("atlas:stream:hydra:price", "rag_group"),
            return_exceptions=True,
        )
        self._task = asyncio.create_task(self._loop())
        logger.info("MultiAssetRunner started")

    async def close(self) -> None:
        """Stop the event-driven loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                if not self._task.cancelled():
                    raise

    async def _loop(self) -> None:
        """Listen to HYDRA streams without sleeping timers."""
        streams: dict[Any, Any] = {
            "atlas:stream:hydra:cascades": ">",
            "atlas:stream:hydra:price": ">",
        }
        while self._running:
            try:
                results = await self._redis_streams.xreadgroup(
                    groupname="scoring_group",
                    consumername="multi_runner",
                    streams=streams,
                    count=10,
                    block=5000,
                )
                if not results:
                    continue
                await self._handle_results(results)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Runner loop error | error={}", str(exc))

    async def _handle_results(self, results: list[Any]) -> None:
        """Parse results, acknowledge, and process events."""
        messages = self._consumer._parse_results(results)

        pipe = self._redis_streams.pipeline()
        has_acks = False
        for msg in messages:
            pipe.xack(msg.stream, "scoring_group", msg.entry_id)
            has_acks = True
        
        if has_acks:
            await pipe.execute()

        await self._process_events(messages)

    async def _process_events(self, messages: list[StreamMessage]) -> None:
        """Process stream events and trigger orchestrators."""
        pending_signals: list[SignalOutput] = []
        for msg in messages:
            try:
                # payload_bytes are msgspec bytes, we can decode generically as dict 
                # since we only need the asset symbol here
                data = msgspec.json.decode(msg.payload_bytes)
                asset = data.get("asset")
                if not asset:
                    continue
                if asset in [a.symbol for a in self._active_assets]:
                    sig = self._mock_signal_generation(asset)
                    if sig:
                        pending_signals.append(sig)
            except Exception as exc:
                logger.error("Error decoding message payload | error={}", str(exc))

        if not pending_signals:
            return

        filtered_signals = self._apply_portfolio_filter(pending_signals)
        for sig in filtered_signals:
            await self._publisher.publish(sig)

    def _mock_signal_generation(
        self, asset: str,
    ) -> SignalOutput | None:
        """Mock pipeline signal for tests (in real life, spawns agent)."""
        return None  # Replaced in tests

    def _apply_portfolio_filter(
        self, signals: list[SignalOutput],
    ) -> list[SignalOutput]:
        """Suppress identical correlated signals; penalise herding."""
        asset_map = {a.symbol: a for a in self._active_assets}
        group_decisions = _build_group_decisions(signals, asset_map)

        filtered: list[SignalOutput] = []
        for (group, decision), sigs in group_decisions.items():
            filtered.extend(_filter_herding_group(group, decision, sigs))
        return filtered


def _build_group_decisions(
    signals: list[SignalOutput], asset_map: dict[str, Any],
) -> dict[tuple[str, SignalDecision], list[SignalOutput]]:
    """Group signals by (asset_group, decision)."""
    groups: dict[tuple[str, SignalDecision], list[SignalOutput]] = {}
    for sig in signals:
        ac = asset_map.get(sig.asset)
        if not ac:
            continue
        key = (ac.group, sig.decision)
        groups.setdefault(key, []).append(sig)
    return groups


_DIRECTIONAL_DECISIONS = {
    SignalDecision.BUY, SignalDecision.STRONG_BUY,
    SignalDecision.SELL, SignalDecision.STRONG_SELL,
}


def _filter_herding_group(
    group: str, decision: SignalDecision, sigs: list[SignalOutput],
) -> list[SignalOutput]:
    """Suppress lower-conviction signals within a herding group."""
    if len(sigs) > 1 and decision in _DIRECTIONAL_DECISIONS:
        logger.warning(
            "Herding Risk in group {} | decision={} count={}",
            group, decision.value, len(sigs),
        )
        best = max(sigs, key=lambda s: s.score)
        for s in sigs:
            if s != best:
                logger.info("Suppressed lower-conviction | asset={}", s.asset)
        return [best]
    return list(sigs)
