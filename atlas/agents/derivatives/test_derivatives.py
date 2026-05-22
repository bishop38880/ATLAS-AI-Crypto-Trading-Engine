"""DerivativesAgent rewrite tests — scalar scoring validation."""

import pytest
from typing import Any
from atlas.agents.derivatives.derivatives_agent import DerivativesAgent

def _make_data(
    zscore: float = 0.0,
    oi_change_4h: float = 0.0,
    oi_trend: str = "neutral",
    basis_annualised: float = 0.0,
    basis_signal: str = "neutral",
) -> dict[str, Any]:
    return {
        "zscore": zscore,
        "oi_change_4h": oi_change_4h,
        "oi_trend": oi_trend,
        "basis_annualised": basis_annualised,
        "basis_signal": basis_signal,
    }


@pytest.mark.asyncio
async def test_funding_zscore_noise_band_returns_zero():
    """Test: |zscore| = 0.8 returns exactly 0.0 points."""
    agent = DerivativesAgent()
    assert agent._score_funding(0.8) == 0.0
    
    # Also verify full score with isolated zscore
    data = _make_data(zscore=0.8, basis_signal="none")  # none -> 0 basis pts
    res = await agent.score(data, {})
    assert res.score == 0

@pytest.mark.asyncio
async def test_basis_backwardation():
    """Test: backwardation returns max basis points."""
    agent = DerivativesAgent()
    assert agent._score_basis(0.0, "backwardation") == 10.0

@pytest.mark.asyncio
async def test_max_possible_score():
    """Test: Maximum possible score caps at 45 (derivatives agent budget)."""
    data = _make_data(
        zscore=3.0,                  # 30 pts
        oi_change_4h=5.0,            # 15 pts
        oi_trend="expanding",
        basis_annualised=40.0,       # 10 pts contango
        basis_signal="contango",     # total raw = 55 → cap 45
    )
    agent = DerivativesAgent()
    res = await agent.score(data, {})
    assert res.score == 45

@pytest.mark.asyncio
async def test_score_caps_at_45():
    """Test: Even if scores could somehow sum higher, it caps at 45."""
    data = _make_data(
        zscore=5.0,                  # 30 pts
        oi_change_4h=10.0,           # 15 pts
        oi_trend="expanding",
        basis_annualised=50.0,       # 10 pts
        basis_signal="backwardation",# 10 pts -> total 55 pts
    )
    agent = DerivativesAgent()
    res = await agent.score(data, {})
    assert res.score <= 45

@pytest.mark.asyncio
async def test_missing_data_returns_zero():
    data = {}
    agent = DerivativesAgent()
    res = await agent.score(data, {})
    assert res.score == 0

@pytest.mark.asyncio
async def test_missing_columns_safe_extraction():
    data = {"random": 1}
    agent = DerivativesAgent()
    res = await agent.score(data, {})
    assert res.score == 0
