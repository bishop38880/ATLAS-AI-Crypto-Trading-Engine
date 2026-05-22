"""Tests for pipeline/context_assembler.py — S3-P7.

All RAG calls and Redis are mocked. Tests validate route-dependent
context assembly, truncation, and graceful degradation.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from atlas.models.signal import AgentResult
from atlas.pipeline.context_assembler import (
    AssembledContext,
    ContextAssembler,
    _build_user_message,
    _estimate_tokens,
    _format_agent_verdicts,
    _format_live_data,
    _infer_category,
)
from atlas.pipeline.query_classifier import QueryClassification
from atlas.shared.config import PolarisSettings


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def settings() -> PolarisSettings:
    """Minimal PolarisSettings for context assembler tests."""
    return PolarisSettings()


@pytest.fixture()
def mock_rag() -> MagicMock:
    """Mock RAGPipeline with async query_context."""
    rag = MagicMock()
    doc = MagicMock()
    doc.text_content = "BTC scored 85/100 on 2024-01-15 with strong momentum."
    rag.query_context = AsyncMock(return_value=[doc])
    return rag


@pytest.fixture()
def mock_redis() -> AsyncMock:
    """Mock redis.asyncio.Redis client."""
    return AsyncMock()


@pytest.fixture()
def assembler(
    settings: PolarisSettings,
    mock_rag: MagicMock,
    mock_redis: AsyncMock,
) -> ContextAssembler:
    """ContextAssembler with mocked dependencies."""
    return ContextAssembler(
        settings=settings,
        rag_pipeline=mock_rag,
        redis_client=mock_redis,
        max_context_tokens=8000,
    )


def _make_classification(
    route: str,
) -> QueryClassification:
    """Build a frozen QueryClassification with the given route."""
    return QueryClassification(
        route=route,  # type: ignore[arg-type]
        confidence=0.9,
        detected_signals=["test"],
        fallback_to_hybrid=False,
        cache_hit=False,
        query_hash="abc123",
    )


def _make_agent_results() -> list[AgentResult]:
    """Two sample agent results in different categories."""
    return [
        AgentResult(
            agent_name="DerivativesAgent",
            score=40,
            max_score=50,
            explanation="Funding rate strongly positive.",
        ),
        AgentResult(
            agent_name="TechnicalAgent",
            score=45,
            max_score=55,
            explanation="RSI momentum breakout confirmed.",
        ),
    ]


def _make_provider_snapshots() -> dict[str, dict]:
    """Sample provider snapshots with live data."""
    return {
        "derivatives": {
            "funding_rate": "0.015%",
            "open_interest": "$5.2B",
        },
        "technical": {
            "rsi_14": 72.5,
            "macd_signal": "bullish",
        },
    }


# ---------------------------------------------------------------------------
# Test 1: RAG_ONLY route
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_rag_only_route(
    assembler: ContextAssembler,
    mock_rag: MagicMock,
) -> None:
    """RAG_ONLY → rag section non-empty, live section empty."""
    result = await assembler.assemble(
        query="What happened last time BTC hit 60k?",
        classification=_make_classification("RAG_ONLY"),
        agent_results=_make_agent_results(),
        provider_snapshots=_make_provider_snapshots(),
        asset="BTCUSDT",
    )

    assert isinstance(result, AssembledContext)
    assert result.context_sections["rag"] != ""
    assert result.context_sections["live"] == ""
    assert result.rag_documents_used == 1
    assert result.route_used == "RAG_ONLY"
    mock_rag.query_context.assert_awaited_once()


# ---------------------------------------------------------------------------
# Test 2: MCP_ONLY route
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_mcp_only_route(
    assembler: ContextAssembler,
    mock_rag: MagicMock,
) -> None:
    """MCP_ONLY → live non-empty, rag empty, query_context NOT called."""
    result = await assembler.assemble(
        query="What is BTC funding rate right now?",
        classification=_make_classification("MCP_ONLY"),
        agent_results=_make_agent_results(),
        provider_snapshots=_make_provider_snapshots(),
        asset="BTCUSDT",
    )

    assert result.context_sections["live"] != ""
    assert result.context_sections["rag"] == ""
    assert result.rag_documents_used == 0
    assert result.route_used == "MCP_ONLY"
    mock_rag.query_context.assert_not_awaited()


# ---------------------------------------------------------------------------
# Test 3: HYBRID route
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_hybrid_route(
    assembler: ContextAssembler,
    mock_rag: MagicMock,
) -> None:
    """HYBRID → both rag and live sections non-empty."""
    result = await assembler.assemble(
        query="Compare current BTC to historical patterns.",
        classification=_make_classification("HYBRID"),
        agent_results=_make_agent_results(),
        provider_snapshots=_make_provider_snapshots(),
        asset="BTCUSDT",
    )

    assert result.context_sections["rag"] != ""
    assert result.context_sections["live"] != ""
    assert result.rag_documents_used == 1
    assert result.route_used == "HYBRID"
    mock_rag.query_context.assert_awaited_once()


# ---------------------------------------------------------------------------
# Test 4: DIRECT route
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_direct_route(
    assembler: ContextAssembler,
    mock_rag: MagicMock,
) -> None:
    """DIRECT → both empty, query_context NOT called, verdicts present."""
    result = await assembler.assemble(
        query="Explain what confluence scoring is.",
        classification=_make_classification("DIRECT"),
        agent_results=_make_agent_results(),
        provider_snapshots=_make_provider_snapshots(),
        asset="BTCUSDT",
    )

    assert result.context_sections["rag"] == ""
    assert result.context_sections["live"] == ""
    assert result.rag_documents_used == 0
    assert result.route_used == "DIRECT"
    assert "AGENT VERDICTS:" in result.context_sections["agents"]
    mock_rag.query_context.assert_not_awaited()


# ---------------------------------------------------------------------------
# Test 5: Truncation at max_context_tokens=50
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_truncation_applied(
    settings: PolarisSettings,
    mock_redis: AsyncMock,
) -> None:
    """Long rag_section with tight budget → truncation applied.

    The system prompt is ~90 tokens and agent header ~10 tokens.
    A budget of 150 forces RAG section truncation from 20 docs.
    """
    rag = MagicMock()
    docs = []
    for i in range(20):
        doc = MagicMock()
        doc.text_content = (
            f"Document {i}: BTC showed strong momentum with RSI at {70 + i} "
            f"and MACD crossing above signal line on 2024-0{(i % 9) + 1}-15."
        )
        docs.append(doc)
    rag.query_context = AsyncMock(return_value=docs)

    assembler = ContextAssembler(
        settings=settings,
        rag_pipeline=rag,
        redis_client=mock_redis,
        max_context_tokens=150,
    )

    result = await assembler.assemble(
        query="Analysis?",
        classification=_make_classification("RAG_ONLY"),
        agent_results=[],
        provider_snapshots={},
        asset="BTCUSDT",
    )

    # Truncation must have occurred — 20 full docs would be ~500+ tokens
    assert result.estimated_tokens <= 150
    # RAG section must be shorter than the full 20-doc version
    rag_section = result.context_sections["rag"]
    rag_doc_count = rag_section.count("[") if rag_section else 0
    assert rag_doc_count < 20


# ---------------------------------------------------------------------------
# Test 6: Degraded provider snapshots
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_degraded_provider_snapshots(
    assembler: ContextAssembler,
) -> None:
    """Empty provider dict → 'N/A (provider degraded)', no exception."""
    result = await assembler.assemble(
        query="What is the current funding rate?",
        classification=_make_classification("MCP_ONLY"),
        agent_results=_make_agent_results(),
        provider_snapshots={"derivatives": {}},
        asset="BTCUSDT",
    )

    live_section = result.context_sections["live"]
    assert "N/A (provider degraded)" in live_section


# ---------------------------------------------------------------------------
# Test 7: Empty agent_results
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_empty_agent_results(
    assembler: ContextAssembler,
) -> None:
    """agent_results=[] → 'No agent results this cycle.', no exception."""
    result = await assembler.assemble(
        query="What happened last time?",
        classification=_make_classification("RAG_ONLY"),
        agent_results=[],
        provider_snapshots={},
        asset="BTCUSDT",
    )

    agents_section = result.context_sections["agents"]
    assert "No agent results this cycle." in agents_section


# ---------------------------------------------------------------------------
# Test 8: RAG pipeline exception (degraded fallback)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_rag_pipeline_exception(
    settings: PolarisSettings,
    mock_redis: AsyncMock,
) -> None:
    """RAG pipeline raises → fallback string, 0 docs, no propagation."""
    rag = MagicMock()
    rag.query_context = AsyncMock(
        side_effect=RuntimeError("Qdrant connection refused"),
    )

    assembler = ContextAssembler(
        settings=settings,
        rag_pipeline=rag,
        redis_client=mock_redis,
        max_context_tokens=8000,
    )

    result = await assembler.assemble(
        query="What happened last time BTC hit 60k?",
        classification=_make_classification("RAG_ONLY"),
        agent_results=_make_agent_results(),
        provider_snapshots={},
        asset="BTCUSDT",
    )

    assert "pipeline error" in result.context_sections["rag"]
    assert result.rag_documents_used == 0
    assert result.route_used == "RAG_ONLY"


# ---------------------------------------------------------------------------
# Unit tests for pure helper functions
# ---------------------------------------------------------------------------


def test_estimate_tokens() -> None:
    """Token estimation is ~1.3x word count."""
    text = "hello world this is a test"
    result = _estimate_tokens(text)
    assert result == int(6 * 1.3)


def test_format_agent_verdicts_empty() -> None:
    """Empty results → placeholder string."""
    result = _format_agent_verdicts([])
    assert "No agent results this cycle." in result


def test_format_agent_verdicts_uses_v61_category_caps() -> None:
    """Whale + regime agents map to onchain (65) and market_context (30) caps."""
    results = [
        AgentResult(
            agent_name="whale",
            score=20,
            max_score=65,
            explanation="smart-money bias",
        ),
        AgentResult(
            agent_name="regime",
            score=10,
            max_score=30,
            explanation="risk-on",
        ),
    ]
    text = _format_agent_verdicts(results)
    assert "ONCHAIN (20.0/65):" in text
    assert "MARKET_CONTEXT (10.0/30):" in text


def test_infer_category_macro_and_funding_aliases() -> None:
    """Explicit substring routing avoids mis-bucketing macro and funding agents."""
    assert _infer_category("news_macro") == "market_context"
    assert _infer_category("funding_rate_monitor") == "derivatives"


def test_format_live_data_empty_snapshots() -> None:
    """Empty snapshots → 'No provider data available.'"""
    result = _format_live_data({}, "BTCUSDT")
    assert "No provider data available." in result


def test_build_user_message_ordering() -> None:
    """User message follows agents → live → rag → query order."""
    msg = _build_user_message(
        query="test query",
        rag_section="RAG DATA",
        live_section="LIVE DATA",
        agents_section="AGENT DATA",
    )
    agent_pos = msg.index("AGENT DATA")
    live_pos = msg.index("LIVE DATA")
    rag_pos = msg.index("RAG DATA")
    query_pos = msg.index("QUERY: test query")

    assert agent_pos < live_pos < rag_pos < query_pos


def test_assembled_context_is_frozen() -> None:
    """AssembledContext is immutable (frozen=True)."""
    ctx = AssembledContext(
        system_prompt="test",
        user_message="test",
        context_sections={},
        route_used="DIRECT",
        asset="BTCUSDT",
    )
    with pytest.raises(Exception):
        ctx.system_prompt = "mutated"  # type: ignore[misc]
