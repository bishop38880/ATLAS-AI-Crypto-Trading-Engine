"""Tests for SynthesisOutput schema and LLM response parser."""

from __future__ import annotations

import pytest

from atlas.llm.response_parser import extract_json_from_response, parse_synthesis_output
from atlas.schemas.synthesis_output import (
    AgentProvenance,
    Archetype,
    DimensionBreakdown,
    SynthesisOutput,
    TradeDecision,
)

VALID_PROVENANCE = AgentProvenance(
    derivatives_agent="DerivativesAgent/v1.0",
    whale_agent="WhaleWatcherAgent/v1.0",
    sentiment_agent="SentimentAgent/v1.0",
    macro_agent="MacroAgent/v1.0",
    technical_agent="TechnicalAgent/v1.0",
    synthesis_model="deepseek-r1",
)


def valid_output(**kwargs: object) -> SynthesisOutput:
    defaults: dict[str, object] = {
        "confluence_total": 165,
        "dimension_breakdown": DimensionBreakdown(
            derivatives=70,
            whale=55,
            sentiment=20,
            macro=15,
            technical=5,
        ),
        "archetype": Archetype.CAPITULATION_REVERSAL,
        "decision": TradeDecision.BUY,
        "direction": "long",
        "confidence_score": 0.85,
        "active_flags": ["RETAIL_DESPAIR", "CASCADE_EXHAUSTION"],
        "provenance": VALID_PROVENANCE,
        "latency_ms": 1200,
    }
    defaults.update(kwargs)
    return SynthesisOutput(**defaults)


def test_valid_output() -> None:
    out = valid_output()
    assert out.confluence_total == 165


def test_dimension_sum_mismatch_raises() -> None:
    with pytest.raises(Exception):
        valid_output(confluence_total=100)


def test_contradiction_must_be_no_trade() -> None:
    with pytest.raises(Exception):
        valid_output(
            archetype=Archetype.CONTRADICTION,
            decision=TradeDecision.BUY,
        )


def test_contradiction_no_trade_passes() -> None:
    out = valid_output(
        archetype=Archetype.CONTRADICTION,
        decision=TradeDecision.NO_TRADE,
    )
    assert out.archetype == Archetype.CONTRADICTION


def test_liquidity_trap_must_be_block() -> None:
    with pytest.raises(Exception):
        valid_output(
            archetype=Archetype.LIQUIDITY_TRAP,
            decision=TradeDecision.BUY,
        )


def test_low_confidence_must_be_watch() -> None:
    with pytest.raises(Exception):
        valid_output(confidence_score=0.3, decision=TradeDecision.BUY)


def test_data_degraded_cap() -> None:
    with pytest.raises(Exception):
        valid_output(
            confluence_total=165,
            dimension_breakdown=DimensionBreakdown(
                derivatives=70,
                whale=55,
                sentiment=20,
                macro=15,
                technical=5,
            ),
            decision=TradeDecision.BUY,
            data_degraded=True,
        )


def test_derivatives_ceiling() -> None:
    with pytest.raises(Exception):
        valid_output(
            confluence_total=180,
            dimension_breakdown=DimensionBreakdown(
                derivatives=76,
                whale=60,
                sentiment=20,
                macro=20,
                technical=4,
            ),
        )


def test_think_block_stripping() -> None:
    raw = '<think>some reasoning here</think>\n{"test": 1}'
    assert extract_json_from_response(raw) == '{"test": 1}'


def test_think_block_with_multiline() -> None:
    raw = (
        "<think>\nlong\nmultiline\nthinking\n</think>\n"
        '{"test": 2}'
    )
    assert extract_json_from_response(raw) == '{"test": 2}'


def test_parse_synthesis_output_round_trip() -> None:
    payload = {
        "confluence_total": 165,
        "dimension_breakdown": {
            "derivatives": 70,
            "whale": 55,
            "sentiment": 20,
            "macro": 15,
            "technical": 5,
        },
        "archetype": "CAPITULATION_REVERSAL",
        "decision": "BUY",
        "direction": "long",
        "confidence_score": 0.85,
        "active_flags": ["RETAIL_DESPAIR"],
        "provenance": {
            "derivatives_agent": "DerivativesAgent/v1.0",
            "whale_agent": "WhaleWatcherAgent/v1.0",
            "sentiment_agent": "SentimentAgent/v1.0",
            "macro_agent": "MacroAgent/v1.0",
            "technical_agent": "TechnicalAgent/v1.0",
            "synthesis_model": "deepseek-r1",
        },
        "latency_ms": 900,
    }
    import msgspec

    raw = msgspec.json.encode(payload).decode()
    parsed = parse_synthesis_output(raw)
    assert parsed.confluence_total == 165
    assert parsed.decision == TradeDecision.BUY
