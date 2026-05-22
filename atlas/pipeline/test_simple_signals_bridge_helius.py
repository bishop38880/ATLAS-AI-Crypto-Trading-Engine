"""Helius path through simple_signals_bridge into confluence scoring."""

from __future__ import annotations

import time

import fakeredis.aioredis
import pytest

from atlas.pipeline.simple_signals_bridge import build_all_signals_from_market_payload_async
from atlas.providers.helius.models import FlowEvent
from atlas.scoring.confluence import ConfluenceScoringEngine, RegimeContext
from atlas.services import solana_flow_tracker
from atlas.core.autonomous_rag_analysis import default_agent_market_data


@pytest.mark.asyncio
async def test_sol_helius_flow_scores_onchain_category() -> None:
    redis_client = fakeredis.aioredis.FakeRedis(decode_responses=False)
    solana_flow_tracker.init_redis(redis_client)

    event = FlowEvent(
        timestamp=time.time(),
        symbol="SOL",
        direction="outflow",
        amount_usd=2_500_000.0,
        amount_native=15_000.0,
        source="webhook",
        exchange="binance",
        from_address="5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9",
        to_address="cold_wallet",
        signature="bridge_test_sig",
    )
    await solana_flow_tracker.record_flow_event(event)

    data = default_agent_market_data("SOL/USDT")
    ctx: dict = {"fear_greed_score": 45}
    all_sig = await build_all_signals_from_market_payload_async(data, ctx, "SOL/USDT")

    assert all_sig.onchain.data_source == "helius"
    assert all_sig.onchain.exchange_netflow_4h < 0

    engine = ConfluenceScoringEngine()
    regime = RegimeContext(regime_label="normal", multiplier_category="normal_conditions")
    result = await engine.calculate(all_sig, regime)
    onchain_score = result.breakdown["onchain"].score
    assert onchain_score > 0
    assert result.data_source_map["onchain"] == "helius"

    solana_flow_tracker.init_redis(None)  # type: ignore[arg-type]
