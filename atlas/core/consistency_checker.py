"""ConsistencyChecker — Cross-source data consistency validation.

Detects divergence between providers reporting overlapping metrics.
"""

from __future__ import annotations

from decimal import Decimal
from pydantic import BaseModel, ConfigDict
from loguru import logger


class ConsistencyResult(BaseModel):
    """Immutable Pydantic model for consistency results."""

    model_config = ConfigDict(frozen=True)

    is_consistent: bool
    max_divergence_pct: float
    divergent_providers: list[str]


class ConsistencyChecker:
    """Checks cross-source consistency for overlapping metrics.
    
    Known overlaps:
    - spot_price: pyth_hermes + coingecko (and optionally coinalyze;
      >0.5% divergence = warning)
    - open_interest: coinalyze + okx (>2.0% divergence = warning)
    - funding_rate: coinalyze + okx (>2.0% divergence = warning)
    """

    THRESHOLDS = {
        "spot_price": 0.5,
        "open_interest": 2.0,
        "funding_rate": 2.0,
    }

    async def check_consistency(self, metric: str, values: dict[str, Decimal]) -> ConsistencyResult:
        """Check if values from multiple providers diverge beyond threshold."""
        if not values:
            return ConsistencyResult(is_consistent=True, max_divergence_pct=0.0, divergent_providers=[])

        threshold = self.THRESHOLDS.get(metric, 1.0)
        min_provider, min_val = min(values.items(), key=lambda x: x[1])
        max_provider, max_val = max(values.items(), key=lambda x: x[1])

        if min_val == Decimal("0"):
            divergence_pct = 0.0 if max_val == Decimal("0") else 100.0
        else:
            divergence_pct = float(abs((max_val - min_val) / min_val) * 100)

        is_consistent = divergence_pct <= threshold
        divergent_providers: list[str] = []
        if not is_consistent:
            divergent_providers = [min_provider, max_provider]
            logger.warning(
                "consistency divergence detected | metric={} | divergence={:.2f}% | providers={}",
                metric, divergence_pct, divergent_providers,
            )

        return ConsistencyResult(
            is_consistent=is_consistent,
            max_divergence_pct=divergence_pct,
            divergent_providers=divergent_providers,
        )
