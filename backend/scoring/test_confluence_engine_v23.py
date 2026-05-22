"""Unit tests for ConfluenceScoringEngine v2.3 (integer caps + factors)."""

from __future__ import annotations

from backend.schemas.atlas_signals import ConfluenceV23PipelineInput
from backend.scoring.confluence_engine_v23 import (
    CAP_DERIVATIVES,
    MAX_TOTAL,
    compute_confluence_v23,
)


def test_per_category_caps_apply_with_factors() -> None:
    inp = ConfluenceV23PipelineInput(
        derivatives_raw=500,
        onchain_raw=50,
        technical_raw=45,
        sentiment_raw=30,
        market_context_raw=20,
        sentiment_gate_active=True,
    )
    out = compute_confluence_v23(inp)
    assert out.total_points == MAX_TOTAL
    by = {c.name: c.points for c in out.categories}
    assert by["derivatives"] == CAP_DERIVATIVES
    assert by["onchain"] == 50
    assert by["technical"] == 45
    assert by["sentiment"] == 30
    assert by["market_context"] == 20
    assert "derivatives:raw=500 -> clamped=75 (cap=75)" in out.factors


def test_sentiment_gate_zeros_pillar() -> None:
    inp = ConfluenceV23PipelineInput(
        derivatives_raw=10,
        onchain_raw=10,
        technical_raw=10,
        sentiment_raw=25,
        market_context_raw=10,
        sentiment_gate_active=False,
    )
    out = compute_confluence_v23(inp)
    by = {c.name: c.points for c in out.categories}
    assert by["sentiment"] == 0
    assert any("sentiment:gated_inactive" in f for f in out.factors)


def test_strength_label_moderate_band() -> None:
    inp = ConfluenceV23PipelineInput(
        derivatives_raw=40,
        onchain_raw=40,
        technical_raw=40,
        sentiment_raw=15,
        market_context_raw=15,
    )
    out = compute_confluence_v23(inp)
    assert out.total_points == 150
    assert out.strength_label == "MODERATE"
