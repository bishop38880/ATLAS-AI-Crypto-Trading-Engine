"""Tests for OptionsIntelligenceAgent scoring."""

from __future__ import annotations

from decimal import Decimal

import pytest

from atlas.agents.options.options_agent import OptionsIntelligenceAgent
from atlas.providers.deribit.models import OptionsIntelligence, TermStructurePoint


def _intel(
    spot_pct: str = "6",
    pcr_vol: str = "1.6",
    iv_skew: str = "6",
    contango: bool = False,
) -> OptionsIntelligence:
    return OptionsIntelligence(
        asset="BTC",
        max_pain_price=Decimal("90000"),
        put_call_ratio_volume=Decimal(pcr_vol),
        put_call_ratio_oi=Decimal("1.1"),
        term_structure=[
            TermStructurePoint(expiry_days=7, atm_iv=Decimal("0.6"), expiry_label="1W"),
        ],
        iv_skew=Decimal(iv_skew),
        contango=contango,
        spot_to_max_pain_pct=Decimal(spot_pct),
    )


@pytest.mark.asyncio
async def test_non_btc_eth_returns_zero_max_score() -> None:
    agent = OptionsIntelligenceAgent()
    result = await agent.score({"asset": "SOLUSDT"}, {})
    assert result.score == 0
    assert result.max_score == 0
    assert "not covered" in result.explanation


@pytest.mark.asyncio
async def test_long_eval_scores_pcr_fear_contrarian() -> None:
    agent = OptionsIntelligenceAgent()
    result = await agent.score(
        {"asset": "BTCUSDT", "options_intelligence": _intel(pcr_vol="1.6")},
        {"trade_bias": "long"},
    )
    assert result.score >= 4
    assert result.max_score == 18


@pytest.mark.asyncio
async def test_short_eval_scores_high_iv_skew() -> None:
    agent = OptionsIntelligenceAgent()
    result = await agent.score(
        {"asset": "ETHUSDT", "options_intelligence": _intel(iv_skew="6")},
        {"trade_bias": "short"},
    )
    assert result.score >= 4


@pytest.mark.asyncio
async def test_backwardation_adds_term_structure_points() -> None:
    agent = OptionsIntelligenceAgent()
    result = await agent.score(
        {
            "asset": "BTCUSDT",
            "options_intelligence": _intel(contango=False, spot_pct="0"),
        },
        {"trade_bias": "long"},
    )
    assert result.score >= 3


def test_max_points_property() -> None:
    agent = OptionsIntelligenceAgent()
    assert agent.max_points == 18
