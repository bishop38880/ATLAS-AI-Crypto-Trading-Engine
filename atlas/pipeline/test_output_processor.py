"""Tests for OutputProcessor — S3-P9.

9 tests covering:
  1. High score → STRONG_BUY, emit_to_prometheus=True
  2. Low score → NO_POSITION, emit_to_prometheus=False
  3. Structured LLM headers → correct extraction
  4. Unstructured LLM text → WARNING logged, empty lists
  5. PIPELINE_ERROR → NO_POSITION, no Redis publish
  6. confidence > 1.0 → validation fails, NO_POSITION
  7. STRONG_BUY → activity LPUSH + LTRIM called
  8. Redis publish raises → CRITICAL log, no exception
  9. msgspec.json.encode used (NOT json.dumps)
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import msgspec
import pytest

from atlas.models.signal import (
    AgentResult,
    CategoryScores,
    SignalDecision,
    SignalOutput,
    ActionBlock,
)
from atlas.models.telemetry import TelemetryEvent
from atlas.pipeline.llm_router import LLMResponse
from atlas.pipeline.output_processor import (
    OutputProcessor,
    ProcessedSignal,
)
from atlas.pipeline.pubsub import RedisSignalPublisher


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_signal(
    score: int = 85,
    confidence: Decimal = Decimal("0.80"),
    decision: SignalDecision = SignalDecision.STRONG_BUY,
) -> SignalOutput:
    """Build a minimal valid SignalOutput for testing."""
    now = datetime.now(timezone.utc)
    actionable = decision in (
        SignalDecision.STRONG_BUY,
        SignalDecision.BUY,
        SignalDecision.SELL,
        SignalDecision.STRONG_SELL,
    )
    action = ActionBlock(
        side="buy",
        order_type="limit",
        price=Decimal("65000.00"),
        stop_loss=Decimal("63000.00"),
        take_profit=Decimal("70000.00"),
    ) if actionable else None
    return SignalOutput(
        decision=decision,
        asset="BTCUSDT",
        score=score,
        confidence=confidence,
        category_scores=CategoryScores(
            technical=20,
            derivatives=15,
            onchain=10,
            sentiment=5,
            whale=5,
            liquidation=5,
            regime=5,
            funding=5,
            news_macro=5,
            correlation=5,
            context=5,
            total=85,
        ),
        telemetry=TelemetryEvent(
            cycle_id="test-cycle-001",
            cycle_latency_ms=150.0,
            agent_count=10,
        ),
        expires_at=now + timedelta(minutes=30),
        action=action,
        timestamp=now,
    )


def _make_llm_response(
    raw_text: str = "REASONING: Strong bullish setup.\nCONVERGENCES:\n- OBV divergence\n- Funding reset\nRISKS:\n- Macro uncertainty\nNEXT ACTIONS:\n- Watch for breakout",
    model_used: str = "deepseek-v3",
    escalation_reason: str | None = None,
) -> LLMResponse:
    """Build a minimal LLMResponse for testing."""
    return LLMResponse(
        raw_text=raw_text,
        model_used=model_used,
        escalation_reason=escalation_reason,
        input_tokens_est=500,
        output_tokens_est=200,
        latency_ms=350.0,
        route_decision="LOCAL",
    )


def _make_agent_results() -> list[AgentResult]:
    """Build minimal agent results for testing."""
    return [
        AgentResult(
            agent_name="TechnicalAgent",
            score=40,
            max_score=55,
            explanation="Bullish momentum",
        ),
    ]


@pytest.fixture()
def mock_settings() -> MagicMock:
    """Mock PolarisSettings."""
    settings = MagicMock()
    settings.redis_url = "redis://localhost:6379/0"
    settings.min_trade_score = 68
    return settings


@pytest.fixture()
def mock_rag_pipeline() -> AsyncMock:
    """Mock RAGPipeline."""
    rag = AsyncMock()
    rag.write_signal_memory = AsyncMock(return_value="doc-id-001")
    return rag


@pytest.fixture()
def mock_signal_publisher() -> AsyncMock:
    """Mock RedisSignalPublisher."""
    return AsyncMock(spec=RedisSignalPublisher)


@pytest.fixture()
def mock_redis() -> AsyncMock:
    """Mock redis.asyncio.Redis."""
    redis = AsyncMock()
    pipe = AsyncMock()
    pipe.lpush = MagicMock(return_value=pipe)
    pipe.ltrim = MagicMock(return_value=pipe)
    pipe.execute = AsyncMock(return_value=[1, True])
    redis.pipeline = MagicMock(return_value=pipe)
    redis.publish = AsyncMock(return_value=1)
    return redis


@pytest.fixture()
def processor(
    mock_settings: MagicMock,
    mock_rag_pipeline: AsyncMock,
    mock_signal_publisher: AsyncMock,
    mock_redis: AsyncMock,
) -> OutputProcessor:
    """Build an OutputProcessor with all mocks."""
    return OutputProcessor(
        settings=mock_settings,
        rag_pipeline=mock_rag_pipeline,
        signal_publisher=mock_signal_publisher,
        redis_client=mock_redis,
    )


# ---------------------------------------------------------------------------
# Test 1: score=85 → STRONG_BUY, emit_to_prometheus=True
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_high_score_strong_buy(
    processor: OutputProcessor,
    mock_redis: AsyncMock,
) -> None:
    """Score 85 → STRONG_BUY with emission to PROMETHEUS."""
    signal = _make_signal(score=85)
    llm_response = _make_llm_response()
    agents = _make_agent_results()

    result = await processor.process(signal, llm_response, agents, "BTCUSDT")

    assert result.decision == "STRONG_BUY"
    assert result.emit_to_prometheus is True
    mock_redis.publish.assert_called_once()


# ---------------------------------------------------------------------------
# Test 2: score=45 → NO_POSITION, emit_to_prometheus=False
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_low_score_no_position(
    processor: OutputProcessor,
    mock_redis: AsyncMock,
    mock_rag_pipeline: AsyncMock,
) -> None:
    """Score 45 → NO_POSITION with no emission."""
    signal = _make_signal(
        score=45,
        decision=SignalDecision.NO_POSITION,
    )
    llm_response = _make_llm_response()
    agents = _make_agent_results()

    result = await processor.process(signal, llm_response, agents, "BTCUSDT")

    assert result.decision == "NO_POSITION"
    assert result.emit_to_prometheus is False
    mock_redis.publish.assert_not_called()
    mock_rag_pipeline.write_signal_memory.assert_not_called()


# ---------------------------------------------------------------------------
# Test 3: Structured LLM headers → correct extraction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_structured_llm_extraction(
    processor: OutputProcessor,
) -> None:
    """LLM text with headers → convergences/risks extracted correctly."""
    signal = _make_signal(score=85)
    llm_response = _make_llm_response(
        raw_text=(
            "REASONING: Strong bullish confluence detected.\n"
            "CONVERGENCES:\n"
            "- OBV divergence bullish\n"
            "- Funding rate reset\n"
            "RISKS:\n"
            "- Macro headwinds\n"
            "- Low volume\n"
            "NEXT ACTIONS:\n"
            "- Monitor breakout level"
        ),
    )
    agents = _make_agent_results()

    result = await processor.process(signal, llm_response, agents, "BTCUSDT")

    assert result.reasoning_summary == "Strong bullish confluence detected."
    assert "OBV divergence bullish" in result.key_convergences
    assert "Funding rate reset" in result.key_convergences
    assert "Macro headwinds" in result.key_risks
    assert "Monitor breakout level" in result.suggested_next_actions


# ---------------------------------------------------------------------------
# Test 4: Unstructured LLM text → WARNING logged, empty lists
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_unstructured_llm_warning(
    processor: OutputProcessor,
) -> None:
    """Unstructured LLM output → WARNING logged, empty lists returned."""
    signal = _make_signal(score=85)
    llm_response = _make_llm_response(
        raw_text="Bitcoin looks strong. Multiple indicators are aligned.",
    )
    agents = _make_agent_results()

    with patch("atlas.pipeline.output_processor.logger") as mock_logger:
        result = await processor.process(
            signal, llm_response, agents, "BTCUSDT",
        )

    assert result.key_convergences == []
    assert result.key_risks == []
    assert result.suggested_next_actions == []
    assert "Bitcoin looks strong" in result.reasoning_summary
    mock_logger.warning.assert_called()


# ---------------------------------------------------------------------------
# Test 5: PIPELINE_ERROR → NO_POSITION, no Redis publish
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_pipeline_error_no_position(
    processor: OutputProcessor,
    mock_redis: AsyncMock,
) -> None:
    """LLM PIPELINE_ERROR → NO_POSITION, no publish call."""
    signal = _make_signal(score=85)
    llm_response = _make_llm_response(raw_text="PIPELINE_ERROR")
    agents = _make_agent_results()

    result = await processor.process(signal, llm_response, agents, "BTCUSDT")

    assert result.decision == "NO_POSITION"
    assert result.emit_to_prometheus is False
    assert "Pipeline error" in result.reasoning_summary
    mock_redis.publish.assert_not_called()


# ---------------------------------------------------------------------------
# Test 6: confidence > 1.0 → validation fails, NO_POSITION
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_confidence_too_high_validation_fails(
    processor: OutputProcessor,
) -> None:
    """Confidence > 1.0 → validation fails → NO_POSITION."""
    # Must bypass Pydantic validation to test our own validator
    signal = _make_signal(score=85, confidence=Decimal("0.80"))
    # Monkey-patch the frozen field for this test
    object.__setattr__(signal, "confidence", Decimal("1.5"))
    llm_response = _make_llm_response()
    agents = _make_agent_results()

    with patch("atlas.pipeline.output_processor.logger") as mock_logger:
        result = await processor.process(
            signal, llm_response, agents, "BTCUSDT",
        )

    assert result.decision == "NO_POSITION"
    assert result.emit_to_prometheus is False
    mock_logger.error.assert_called()


# ---------------------------------------------------------------------------
# Test 7: STRONG_BUY → activity LPUSH + LTRIM called
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_activity_stream_lpush_ltrim(
    processor: OutputProcessor,
    mock_redis: AsyncMock,
) -> None:
    """STRONG_BUY signal triggers activity stream LPUSH + LTRIM."""
    signal = _make_signal(score=85)
    llm_response = _make_llm_response()
    agents = _make_agent_results()

    await processor.process(signal, llm_response, agents, "BTCUSDT")

    pipe = mock_redis.pipeline()
    pipe.lpush.assert_called()
    pipe.ltrim.assert_called_with("activity:stream", 0, 999)


# ---------------------------------------------------------------------------
# Test 8: Redis publish raises → CRITICAL log, no exception escapes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_redis_publish_failure_no_exception(
    processor: OutputProcessor,
    mock_redis: AsyncMock,
) -> None:
    """Redis publish failure → CRITICAL logged, no exception escapes."""
    mock_redis.publish = AsyncMock(side_effect=ConnectionError("Redis down"))
    signal = _make_signal(score=85)
    llm_response = _make_llm_response()
    agents = _make_agent_results()

    with patch("atlas.pipeline.output_processor.logger") as mock_logger:
        result = await processor.process(
            signal, llm_response, agents, "BTCUSDT",
        )

    # Should still return a valid ProcessedSignal
    assert result.decision == "STRONG_BUY"
    assert result.emit_to_prometheus is True
    mock_logger.critical.assert_called()


# ---------------------------------------------------------------------------
# Test 9: msgspec.json.encode used (NOT json.dumps)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_msgspec_encode_used(
    processor: OutputProcessor,
    mock_redis: AsyncMock,
) -> None:
    """Verify msgspec.json.encode is invoked for serialization."""
    signal = _make_signal(score=85)
    llm_response = _make_llm_response()
    agents = _make_agent_results()

    with patch("atlas.pipeline.output_processor.msgspec") as mock_msgspec:
        # Provide a real-looking bytes return so downstream doesn't crash
        mock_msgspec.json.encode.return_value = b'{"test": true}'
        await processor.process(signal, llm_response, agents, "BTCUSDT")

    mock_msgspec.json.encode.assert_called()
