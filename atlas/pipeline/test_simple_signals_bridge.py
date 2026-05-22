"""Smoke tests for simple pipeline market → AllSignals bridge."""

from __future__ import annotations

import pytest

from atlas.pipeline.simple_signals_bridge import build_all_signals_from_market_payload
from atlas.scoring.confluence import ConfluenceScoringEngine, RegimeContext

from atlas.core.autonomous_rag_analysis import default_agent_market_data


@pytest.mark.asyncio
async def test_simple_bridge_feeds_confluence_engine() -> None:
    data = default_agent_market_data("BTC/USDT")
    ctx = {"fear_greed_score": 40, "vol_zscore": 3.0}
    all_sig = build_all_signals_from_market_payload(data, ctx)
    engine = ConfluenceScoringEngine()
    regime = RegimeContext(regime_label="normal", multiplier_category="normal_conditions")
    result = await engine.calculate(all_sig, regime)
    assert 0 <= result.total_score <= 220
