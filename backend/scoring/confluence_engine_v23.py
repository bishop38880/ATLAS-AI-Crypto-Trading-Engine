"""ConfluenceScoringEngine v2.3 — integer-only deterministic pillar scoring."""

from __future__ import annotations

from typing import Literal

from loguru import logger

from backend.schemas.atlas_signals import (
    ConfluenceCategoryBreakdownV23,
    ConfluenceScoreBundleV23,
    ConfluenceV23PipelineInput,
    CURRENT_INTELLIGENCE_SCHEMA,
)

CAP_DERIVATIVES: int = 75
CAP_ONCHAIN: int = 50
CAP_TECHNICAL: int = 45
CAP_SENTIMENT: int = 30
CAP_MARKET_CONTEXT: int = 20
MAX_TOTAL: int = 220

assert (
    CAP_DERIVATIVES + CAP_ONCHAIN + CAP_TECHNICAL + CAP_SENTIMENT + CAP_MARKET_CONTEXT
    == MAX_TOTAL
)

_STRONG_MIN: int = 180
_MODERATE_MIN: int = 150
_WEAK_MIN: int = 120


def _clamp_non_negative(value: int) -> int:
    assert isinstance(value, int), "v2.3 inputs must be ints"
    return value if value > 0 else 0


def _apply_cap(raw: int, cap: int, pillar: str) -> tuple[int, tuple[str, ...]]:
    """Clamp ``raw`` to ``cap`` with factor strings (integer-only path)."""
    raw_clean = _clamp_non_negative(raw)
    if raw_clean > cap:
        assigned = cap
        factors_inner = (
            "{}:raw={} -> clamped={} (cap={})".format(pillar, raw_clean, assigned, cap),
        )
        return assigned, factors_inner
    factors_inner = ("{}:raw={} -> assigned={}".format(pillar, raw_clean, raw_clean),)
    return raw_clean, factors_inner


def _classify_strength(total: int) -> Literal["STRONG", "MODERATE", "WEAK", "NO_SIGNAL"]:
    if total >= _STRONG_MIN:
        return "STRONG"
    if total >= _MODERATE_MIN:
        return "MODERATE"
    if total >= _WEAK_MIN:
        return "WEAK"
    return "NO_SIGNAL"


def compute_confluence_v23(
    pipeline_input: ConfluenceV23PipelineInput,
) -> ConfluenceScoreBundleV23:
    """Score five pillars with strict integer caps and mandatory factor ledgers.

    This function never references execution, sizing, or positions.
    """
    assert MAX_TOTAL == 220, "v2.3 requires max total 220"

    factors_acc: list[str] = []

    d_pts, d_facts = _apply_cap(
        pipeline_input.derivatives_raw,
        CAP_DERIVATIVES,
        "derivatives",
    )
    factors_acc.extend(d_facts)

    o_pts, o_facts = _apply_cap(
        pipeline_input.onchain_raw,
        CAP_ONCHAIN,
        "onchain",
    )
    factors_acc.extend(o_facts)

    t_pts, t_facts = _apply_cap(
        pipeline_input.technical_raw,
        CAP_TECHNICAL,
        "technical",
    )
    factors_acc.extend(t_facts)

    if pipeline_input.sentiment_gate_active:
        s_pts, s_facts = _apply_cap(
            pipeline_input.sentiment_raw,
            CAP_SENTIMENT,
            "sentiment",
        )
    else:
        s_pts = 0
        s_facts = (
            "sentiment:gated_inactive -> assigned=0 (gate=False)",
            "sentiment:raw_suppressed={}".format(
                _clamp_non_negative(pipeline_input.sentiment_raw),
            ),
        )
    factors_acc.extend(s_facts)

    m_pts, m_facts = _apply_cap(
        pipeline_input.market_context_raw,
        CAP_MARKET_CONTEXT,
        "market_context",
    )
    factors_acc.extend(m_facts)

    total: int = d_pts + o_pts + t_pts + s_pts + m_pts
    assert total <= MAX_TOTAL, "integer caps must keep total <= 220"
    factors_acc.append("total:integer_sum={}".format(total))

    categories: tuple[ConfluenceCategoryBreakdownV23, ...] = (
        ConfluenceCategoryBreakdownV23(name="derivatives", points=d_pts, factors=d_facts),
        ConfluenceCategoryBreakdownV23(name="onchain", points=o_pts, factors=o_facts),
        ConfluenceCategoryBreakdownV23(name="technical", points=t_pts, factors=t_facts),
        ConfluenceCategoryBreakdownV23(name="sentiment", points=s_pts, factors=s_facts),
        ConfluenceCategoryBreakdownV23(
            name="market_context",
            points=m_pts,
            factors=m_facts,
        ),
    )

    strength = _classify_strength(total)
    logger.info(
        "confluence_v23_scored | total={} | strength={} | gate_active={}",
        total,
        strength,
        pipeline_input.sentiment_gate_active,
    )

    return ConfluenceScoreBundleV23(
        schema_version=CURRENT_INTELLIGENCE_SCHEMA,
        total_points=total,
        categories=categories,
        strength_label=strength,
        factors=tuple(factors_acc),
    )
