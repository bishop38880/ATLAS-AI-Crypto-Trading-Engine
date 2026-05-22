"""POLARIS v6.1 scoring constants — re-export for backend-local imports.

Canonical definitions live in ``atlas.scoring.scoring_weights`` so all
services share one source of truth.
"""

from __future__ import annotations

from atlas.scoring.scoring_weights import (  # noqa: F401
    AGENT_VERDICT_CATEGORY_MAX_POINTS,
    AGENT_VERDICT_CATEGORY_ORDER,
    CATEGORY_MAX_POINTS,
    DEFAULT_GATES,
    MODERATE_MIN,
    SCORING_VERSION,
    STRONG_MIN,
    WEAK_MIN,
    SignalStrengthLabel,
    ScoringGates,
    classify_signal_strength,
    get_leverage_reference,
    get_position_size_pct,
)

__all__ = [
    "AGENT_VERDICT_CATEGORY_MAX_POINTS",
    "AGENT_VERDICT_CATEGORY_ORDER",
    "CATEGORY_MAX_POINTS",
    "DEFAULT_GATES",
    "MODERATE_MIN",
    "SCORING_VERSION",
    "STRONG_MIN",
    "ScoringGates",
    "SignalStrengthLabel",
    "WEAK_MIN",
    "classify_signal_strength",
    "get_leverage_reference",
    "get_position_size_pct",
]
