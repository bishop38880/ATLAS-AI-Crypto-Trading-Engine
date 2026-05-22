"""Post-Trade Learning outcome schema.

Defines the core data contract for trade outcomes pushed by PROMETHEUS
back into the ATLAS intelligence loop.
"""

from __future__ import annotations

from atlas.models.signal import ExitReason, MarketOutcome, OutcomeClassification, TradeOutcome
