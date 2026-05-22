import pytest
from unittest.mock import AsyncMock
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import msgspec

from atlas.pipeline.risk_governor import RiskGovernor, AllocationResult, TIER1_ASSETS, TIER2_ASSETS
from atlas.shared.config import PolarisSettings
from atlas.models.signal import (
    SignalOutput, SignalDecision, CategoryScores, ActionBlock, TelemetryEvent
)

@pytest.fixture
def settings():
    return PolarisSettings()

@pytest.fixture
def mock_redis():
    mock = AsyncMock()
    return mock

def make_signal(asset: str, score: int) -> SignalOutput:
    decision = SignalDecision.NO_POSITION
    action = None
    if score >= 68:
        decision = SignalDecision.BUY
        action = ActionBlock(side="buy", order_type="limit", price=Decimal("100"), stop_loss=Decimal("90"), take_profit=Decimal("110"))
    elif score >= 55:
        decision = SignalDecision.HOLD

    return SignalOutput(
        asset=asset,
        score=score,
        decision=decision,
        action=action,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        category_scores=CategoryScores(),
        telemetry=TelemetryEvent(cycle_id="test", cycle_latency_ms=10.0, agent_count=5),
        confidence=Decimal("0.8"),
    )

@pytest.mark.asyncio
async def test_risk_governor_1(settings, mock_redis):
    open_assets = ["ASSET1", "ASSET2", "ASSET3", "ASSET4", "ASSET5"]
    mock_redis.get.return_value = msgspec.json.encode(open_assets)
    gov = RiskGovernor(settings, mock_redis)
    signals = {f"COIN{i}": make_signal(f"COIN{i}", 60 + i) for i in range(33)}
    
    for a in open_assets:
        signals[a] = make_signal(a, 90)
        
    result = await gov.rank_and_allocate(signals)
    assert len(result.locked) == 5
    assert result.available_slots == 1
    assert len(result.allocated) == 1
    
    # We should ensure the top scorer is selected, ignoring locked ones.
    # The max score is COIN32 with 60 + 32 = 92
    assert result.allocated[0] == "COIN32"

@pytest.mark.asyncio
async def test_risk_governor_2(settings, mock_redis):
    mock_redis.get.return_value = None
    gov = RiskGovernor(settings, mock_redis)
    signals = {"DOGE": make_signal("DOGE", 50), "BTC": make_signal("BTC", 40)}
    result = await gov.rank_and_allocate(signals)
    assert result.allocated == []
    assert len(result.passed) == 2
    assert "DOGE" in result.passed

@pytest.mark.asyncio
async def test_risk_governor_3(settings, mock_redis):
    mock_redis.get.return_value = None
    gov = RiskGovernor(settings, mock_redis, max_open_positions=1)
    signals = {
        "DOGE": make_signal("DOGE", 80),
        "BTC": make_signal("BTC", 80),
    }
    result = await gov.rank_and_allocate(signals)
    assert result.allocated == ["BTC"]

@pytest.mark.asyncio
async def test_risk_governor_4(settings, mock_redis):
    mock_redis.get.side_effect = Exception("ConnectionError")
    gov = RiskGovernor(settings, mock_redis)
    signals = {"BTC": make_signal("BTC", 80)}
    result = await gov.rank_and_allocate(signals)
    assert result.locked == []
    assert result.allocated == ["BTC"]

@pytest.mark.asyncio
async def test_risk_governor_5(settings, mock_redis):
    mock_redis.get.return_value = None
    gov = RiskGovernor(settings, mock_redis, max_open_positions=3)
    signals = {f"COIN{i}": make_signal(f"COIN{i}", 60 + i) for i in range(8)}
    result = await gov.rank_and_allocate(signals)
    assert len(result.allocated) == 3
    assert len(result.queued) == 5
    assert result.allocated[0] == "COIN7"

@pytest.mark.asyncio
async def test_risk_governor_6(settings, mock_redis):
    mock_redis.get.return_value = msgspec.json.encode(["ETH", "SOL"])
    gov = RiskGovernor(settings, mock_redis)
    res = await gov.get_open_positions()
    assert res == ["ETH", "SOL"]
