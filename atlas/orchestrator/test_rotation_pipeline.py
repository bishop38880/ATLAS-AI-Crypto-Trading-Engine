import pytest
from unittest.mock import AsyncMock, patch
import msgspec
from atlas.shared.config import PolarisSettings
from atlas.orchestrator.rotation_pipeline import execute_daily_rotation
from atlas.core.llm_client import LLMResponse

@pytest.fixture
def mock_redis():
    from unittest.mock import AsyncMock, MagicMock
    mock = AsyncMock()
    mock.get.return_value = None
    
    pipe_mock = MagicMock()
    pipe_mock.delete.return_value = pipe_mock
    pipe_mock.sadd.return_value = pipe_mock
    pipe_mock.execute = AsyncMock()
    
    pipe_mock.__aenter__ = AsyncMock(return_value=pipe_mock)
    pipe_mock.__aexit__ = AsyncMock(return_value=None)
    
    mock.pipeline = MagicMock(return_value=pipe_mock)
    
    return mock

@pytest.fixture
def settings():
    return PolarisSettings()

@pytest.mark.asyncio
async def test_execute_daily_rotation_manual_override(mock_redis, settings):
    # Setup manual override
    mock_redis.get.return_value = msgspec.json.encode(["BTC", "ETH", "SOL", "APT"])
    
    top_4 = await execute_daily_rotation(mock_redis, settings)
    
    assert top_4 == ["BTC", "ETH", "SOL", "APT"]
    mock_redis.sadd.assert_called_once_with("atlas:rotation:active", "BTC", "ETH", "SOL", "APT")

@pytest.mark.asyncio
@patch("atlas.orchestrator.rotation_pipeline.LocalLLMClient")
@patch("atlas.orchestrator.rotation_pipeline.DeepSeekClient")
async def test_execute_daily_rotation_auto(mock_deepseek_cls, mock_local_cls, mock_redis, settings):
    # Mock LLMs
    mock_local = mock_local_cls.return_value
    mock_deepseek = mock_deepseek_cls.return_value
    
    # Stage 1 response
    stage1_response = LLMResponse(
        text="The best assets are BTC, ETH, SOL, XRP, ADA, AVAX, DOGE, DOT, LINK, MATIC, LTC, BCH.",
        reasoning=None,
        model="local-model",
        latency_ms=100,
        tokens_used=100,
        cost_estimate_usd=0.0
    )
    mock_local.complete = AsyncMock(return_value=stage1_response)
    
    # Stage 2 response
    stage2_response = LLMResponse(
        text="My top 4 choices are BTC, ETH, SOL, and AVAX based on correlation.",
        reasoning="Here is my reasoning...",
        model="deepseek-reasoner",
        latency_ms=1000,
        tokens_used=500,
        cost_estimate_usd=0.01
    )
    mock_deepseek.complete = AsyncMock(return_value=stage2_response)
    
    top_4 = await execute_daily_rotation(mock_redis, settings)
    
    assert len(top_4) == 4
    assert top_4 == ["BTC", "ETH", "SOL", "AVAX"]
    
    # Check Pipeline Calls
    pipe_mock = mock_redis.pipeline.return_value.__aenter__.return_value
    pipe_mock.delete.assert_called_once_with("atlas:rotation:active")
    pipe_mock.sadd.assert_called_once_with("atlas:rotation:active", "BTC", "ETH", "SOL", "AVAX")
    pipe_mock.execute.assert_called_once()
    
    mock_local.complete.assert_called_once()
    mock_deepseek.complete.assert_called_once()
