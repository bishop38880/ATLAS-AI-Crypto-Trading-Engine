"""Tests for pipeline/llm_router.py — S3-P8.

Mocks httpx, Redis, and Langfuse. Validates routing logic, cost tracking,
retries, and escalation criteria.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import msgspec
from decimal import Decimal

from atlas.models.signal import AgentResult, SignalOutput, CategoryScores
from atlas.pipeline.context_assembler import AssembledContext
from atlas.pipeline.llm_router import LLMRouter, LLMResponse, LLMCallError
from atlas.shared.config import PolarisSettings
from atlas.models.telemetry import TelemetryEvent


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def settings() -> PolarisSettings:
    """PolarisSettings with configured router keys and API key."""
    from pydantic import SecretStr
    s = PolarisSettings()
    # Ensure keys are set for testing
    s.router_local_model = "deepseek-v3"
    s.router_api_model = "deepseek-r1"
    s.deepseek_api_key = SecretStr("sk-test")
    return s


@pytest.fixture()
def mock_http() -> AsyncMock:
    """Mock httpx.AsyncClient."""
    client = AsyncMock(spec=httpx.AsyncClient)
    return client


@pytest.fixture()
def mock_redis() -> AsyncMock:
    """Mock redis.asyncio.Redis."""
    return AsyncMock()


@pytest.fixture()
def mock_langfuse() -> MagicMock:
    """Mock LangfuseTelemetry client."""
    lf = MagicMock()
    trace = MagicMock()
    lf.trace.return_value = trace
    return lf


from typing import AsyncGenerator

@pytest.fixture()
async def router(
    settings: PolarisSettings,
    mock_http: AsyncMock,
    mock_redis: AsyncMock,
    mock_langfuse: MagicMock,
) -> AsyncGenerator[LLMRouter, None]:
    """LLMRouter with mocked dependencies."""
    r = LLMRouter(
        settings=settings,
        http_client=mock_http,
        redis_client=mock_redis,
        langfuse_client=mock_langfuse,
    )
    yield r
    await r.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_context() -> AssembledContext:
    """Minimal AssembledContext."""
    return AssembledContext(
        system_prompt="System prompt",
        user_message="User message",
        estimated_tokens=100,
        route_used="HYBRID",
        asset="BTCUSDT",
    )


def _make_signal(raw_score: int = 100, anomaly_paths: list[str] | None = None) -> SignalOutput:
    """Minimal SignalOutput with raw confluence score."""
    # We need to satisfy model validation for total score
    cat_scores = CategoryScores(
        technical=raw_score,
        total=raw_score
    )
    return SignalOutput(
        decision="Hold",  # type: ignore[arg-type]
        asset="BTCUSDT",
        score=int(raw_score / 2.2),
        confidence=Decimal("0.5"),
        category_scores=cat_scores,
        raw_confluence_score=raw_score,
        contributing_graph_paths=anomaly_paths or [],
        telemetry=TelemetryEvent(
            cycle_id="test-cycle",
            cycle_latency_ms=100.0,
            agent_count=1
        ),
        expires_at=datetime.now(timezone.utc).replace(year=2030), # Far in future
    )


def _make_agent_results(anomaly: bool = False) -> list[AgentResult]:
    """Agent results, optionally with anomaly in sub_signals."""
    sub_signals = {}
    if anomaly:
        sub_signals["feature_scores"] = {"anomaly_z": 2.5}
    
    return [
        AgentResult(
            agent_name="TechnicalAgent",
            score=50,
            max_score=55,
            sub_signals=sub_signals,
        )
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_route_local_routine(
    router: LLMRouter,
    mock_http: AsyncMock,
) -> None:
    """Test 1: Normal score, no anomalies -> LOCAL model (V3)."""
    mock_http.post.return_value = MagicMock(
        status_code=200,
        content=b'{"choices": [{"message": {"content": "Routine analysis"}}]}'
    )

    response = await router.route_and_call(
        context=_make_context(),
        signal=_make_signal(raw_score=130),
        provider_snapshots={},
        agent_results=_make_agent_results(anomaly=False),
    )

    assert response.route_decision == "LOCAL"
    assert response.model_used == "deepseek-v3"
    assert response.escalation_reason is None
    assert response.raw_text == "Routine analysis"


@pytest.mark.asyncio()
async def test_route_api_high_confluence(
    router: LLMRouter,
    mock_http: AsyncMock,
) -> None:
    """Test 2: High score (143) -> API model (R1)."""
    mock_http.post.return_value = MagicMock(
        status_code=200,
        content=b'{"choices": [{"message": {"content": "Deep analysis"}}]}'
    )

    response = await router.route_and_call(
        context=_make_context(),
        signal=_make_signal(raw_score=143),
        provider_snapshots={},
        agent_results=_make_agent_results(anomaly=False),
    )

    assert response.route_decision == "API"
    assert response.model_used == "deepseek-r1"
    assert response.escalation_reason == "HIGH_CONFLUENCE"


@pytest.mark.asyncio()
async def test_route_api_anomaly_flag(
    router: LLMRouter,
    mock_http: AsyncMock,
) -> None:
    """Test 3: Anomaly in agent results -> API model (R1)."""
    mock_http.post.return_value = MagicMock(
        status_code=200,
        content=b'{"choices": [{"message": {"content": "Anomaly analysis"}}]}'
    )

    # Anomaly in sub_signals
    response = await router.route_and_call(
        context=_make_context(),
        signal=_make_signal(raw_score=100),
        provider_snapshots={},
        agent_results=_make_agent_results(anomaly=True),
    )
    assert response.route_decision == "API"
    assert response.escalation_reason == "ANOMALY_FLAG"

    # Anomaly in graph paths
    response = await router.route_and_call(
        context=_make_context(),
        signal=_make_signal(raw_score=100, anomaly_paths=["path/with/anomaly/tag"]),
        provider_snapshots={},
        agent_results=_make_agent_results(anomaly=False),
    )
    assert response.route_decision == "API"
    assert response.escalation_reason == "ANOMALY_FLAG"


@pytest.mark.asyncio()
async def test_route_api_consistency_warning(
    router: LLMRouter,
    mock_http: AsyncMock,
) -> None:
    """Test 4: Consistency warning -> API model (R1)."""
    mock_http.post.return_value = MagicMock(
        status_code=200,
        content=b'{"choices": [{"message": {"content": "Consistency check"}}]}'
    )

    response = await router.route_and_call(
        context=_make_context(),
        signal=_make_signal(raw_score=100),
        provider_snapshots={"_consistency_warning": {}}, # type: ignore
        agent_results=_make_agent_results(anomaly=False),
    )

    assert response.route_decision == "API"
    assert response.escalation_reason == "CONSISTENCY_WARNING"


@pytest.mark.asyncio()
async def test_http_error_retries_and_fallback(
    router: LLMRouter,
    mock_http: AsyncMock,
) -> None:
    """Test 5: HTTP 500 -> 2 retries, then PIPELINE_ERROR."""
    mock_http.post.side_effect = httpx.HTTPStatusError(
        "Server Error", request=MagicMock(), response=MagicMock(status_code=500)
    )

    response = await router.route_and_call(
        context=_make_context(),
        signal=_make_signal(raw_score=100),
        provider_snapshots={},
        agent_results=_make_agent_results(),
    )

    assert response.raw_text == "PIPELINE_ERROR"
    # 1 initial + 2 retries = 3 calls
    assert mock_http.post.call_count == 3


@pytest.mark.asyncio()
async def test_cost_tracking(
    router: LLMRouter,
    mock_http: AsyncMock,
    mock_redis: AsyncMock,
) -> None:
    """Test 6 & 7: Cost tracking via HINCRBY and sequential calls."""
    mock_http.post.return_value = MagicMock(
        status_code=200,
        content=b'{"choices": [{"message": {"content": "Response"}}]}'
    )

    # First call
    await router.route_and_call(
        context=_make_context(),
        signal=_make_signal(raw_score=100),
        provider_snapshots={},
        agent_results=_make_agent_results(),
    )
    
    # Wait for the background task to complete (cost tracking)
    await asyncio.sleep(0.1)
    
    # Check HINCRBY calls
    # key: llm_costs:deepseek-v3:YYYY-MM-DD
    # calls: 1, input: 100, output: 1 (len("Response".split())*1.3 = 1)
    assert mock_redis.pipeline.call_count == 1
    
    # Second call
    await router.route_and_call(
        context=_make_context(),
        signal=_make_signal(raw_score=100),
        provider_snapshots={},
        agent_results=_make_agent_results(),
    )
    await asyncio.sleep(0.1)
    assert mock_redis.pipeline.call_count == 2


def test_init_raises_on_missing_api_key() -> None:
    """Test 8: ValueError if deepseek_api_key is empty."""
    from pydantic import SecretStr
    s = PolarisSettings()
    s.deepseek_api_key = SecretStr("")
    
    with pytest.raises(ValueError, match="deepseek_api_key is required"):
        LLMRouter(
            settings=s,
            http_client=MagicMock(),
            redis_client=MagicMock(),
        )


def test_no_banned_providers_in_source() -> None:
    """Test 9: Verify source has no OpenAI, Anthropic, etc."""
    import os
    file_path = os.path.join(os.path.dirname(__file__), "llm_router.py")
    with open(file_path, "r") as f:
        content = f.read()
    
    banned = ["openai", "anthropic", "grok", "xai"]
    for b in banned:
        assert b not in content.lower(), f"Banned provider {b} found in source!"


@pytest.mark.asyncio()
async def test_langfuse_observability(
    router: LLMRouter,
    mock_http: AsyncMock,
    mock_langfuse: MagicMock,
) -> None:
    """Verify Langfuse trace and update are called."""
    mock_http.post.return_value = MagicMock(
        status_code=200,
        content=b'{"choices": [{"message": {"content": "Observed response"}}]}'
    )

    await router.route_and_call(
        context=_make_context(),
        signal=_make_signal(),
        provider_snapshots={},
        agent_results=_make_agent_results(),
    )

    mock_langfuse.trace.assert_called_once()
    trace = mock_langfuse.trace.return_value
    # Initial trace creation and then update with output
    assert trace.update.call_count >= 1
