"""Cross-correlation grade enum — IM-1 Intelligence Matrix.

ATLAS publishes the correlation grade as part of the signal.
PROMETHEUS maps each grade to its per-trade risk limit:

    STANDARD  → 1.00% risk limit
    ELEVATED  → 1.25% risk limit
    EXTREME   → 1.50% risk limit

ATLAS never calculates the risk percentage itself — it only
classifies the cross-correlation tier.
"""

from __future__ import annotations

from enum import Enum


class CrossCorrelationGrade(str, Enum):
    """Correlation tier — PROMETHEUS maps to risk limit.

    Values:
        STANDARD: Low cross-agent correlation (1.00% risk).
        ELEVATED: Moderate cross-agent correlation (1.25% risk).
        EXTREME: High cross-agent correlation (1.50% risk).
    """

    STANDARD = "STANDARD"
    ELEVATED = "ELEVATED"
    EXTREME = "EXTREME"
