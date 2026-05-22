"""Portfolio Optimisation Agent.

Handles position sizing, correlation slicing, and sector caps.
"""

from __future__ import annotations

import redis.asyncio as redis
from decimal import Decimal

from atlas.agents.base import AgentResult, SignalDirection


class PortfolioOptimisationAgent:
    """Calculates optimal position sizes given portfolio constraints."""

    def __init__(self, redis_client: redis.Redis) -> None:
        self._redis = redis_client
        # Basic mapping of assets to sectors for cap enforcement
        self._sector_map = {
            "SOL/USDT": "L1_ALTS",
            "AVAX/USDT": "L1_ALTS",
            "BTC/USDT": "MAJORS",
            "ETH/USDT": "MAJORS",
        }

    async def calculate_position_size(self, asset: str, analyst_results: list[AgentResult]) -> Decimal:
        """Calculate position sizing based on Tier 1 results and portfolio constraints."""
        if not analyst_results:
            return Decimal("0.0")

        bullish = sum(1 for r in analyst_results if r.direction == SignalDirection.BULLISH)
        bearish = sum(1 for r in analyst_results if r.direction == SignalDirection.BEARISH)
        total = len(analyst_results)

        if total == 0:
            return Decimal("0.0")

        # 1. Base Size & Conviction
        base_size = Decimal("0.10")  # Max 10% base position
        conviction = Decimal(abs(bullish - bearish)) / Decimal(total)
        proposed_size = base_size * conviction

        # 2. Portfolio Correlation Penalty
        corr_raw = await self._redis.get("portfolio:avg_pairwise_correlation")
        avg_correlation = Decimal(corr_raw.decode("utf-8")) if corr_raw else Decimal("0.0")

        is_stressed = avg_correlation > Decimal("0.70")
        if is_stressed:
            # High correlation -> slice position by 50%
            proposed_size *= Decimal("0.5")

        # 3. Sector Caps
        # Enforce Sector Caps: Cap exposure to 25% normal regimes, 15% in high-correlation regimes
        sector_cap = Decimal("0.15") if is_stressed else Decimal("0.25")

        if proposed_size > sector_cap:
            proposed_size = sector_cap

        return proposed_size
