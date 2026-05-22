"""Exit Scoring Engine — S2-P4.

Scores exit signals continuously on every open position each cycle.
Weighted toward **leading indicators** (liquidation reversal, funding
flip, whale outflow, OI divergence). Independent of the 220-point entry
confluence system in both logic and weights.

Publishes ``EXIT_RECOMMENDATION`` signals to ``polaris:signals:{asset}``.
PROMETHEUS decides whether to close.

CRITICAL: This module NEVER modifies the 220-point entry confluence
system. The 100-point exit system is parallel and independent.

Exit thresholds:
    EXIT_STRONG  >= 75  → recommend full close
    EXIT_PARTIAL >= 55  → recommend partial close (50 %)
    EXIT_WATCH   >= 35  → flag, no action
    HOLD         <  35  → no exit signal
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

import msgspec
import redis.asyncio as redis_async
from loguru import logger
from pydantic import BaseModel, ConfigDict

from atlas.pipeline.pubsub import RedisSignalPublisher
from atlas.shared.config import PolarisSettings
from atlas.shared.hydra_asset import hydra_base_asset

# ---------------------------------------------------------------------------
# Exit-point budget
# ---------------------------------------------------------------------------

_LIQUIDATION_MAX: int = 28
_FUNDING_MAX: int = 25
_WHALE_MAX: int = 20
_OI_MAX: int = 15
_TECHNICAL_MAX: int = 12
_EXIT_TOTAL: int = 100  # sum of above

# Thresholds
_STRONG_THRESHOLD: int = 75
_PARTIAL_THRESHOLD: int = 55
_WATCH_THRESHOLD: int = 35


# ---------------------------------------------------------------------------
# Pydantic models (frozen)
# ---------------------------------------------------------------------------


class ExitScoringInputs(BaseModel):
    """Raw data consumed by each exit sub-scorer."""

    model_config = ConfigDict(frozen=True)

    liquidation_cluster_data: dict[str, Decimal]
    funding_rate_current: Decimal
    funding_rate_4h_ago: Decimal
    funding_rate_zscore: float       # dimensionless — float OK
    whale_outflow_zscore: float      # dimensionless — float OK
    oi_change_4h_pct: Decimal
    price_change_4h_pct: Decimal
    timeframe_structure_breaks: int  # 0, 1, or 2
    rsi_divergence_detected: bool


class ExitSignalOutput(BaseModel):
    """Immutable exit recommendation emitted by ATLAS."""

    model_config = ConfigDict(frozen=True)

    asset: str
    position_direction: Literal["LONG", "SHORT"]
    exit_score: int
    exit_recommendation: Literal[
        "EXIT_STRONG", "EXIT_PARTIAL", "EXIT_WATCH", "HOLD",
    ]
    category_breakdown: dict[str, int]
    primary_exit_reason: str
    signal_type: Literal["EXIT_RECOMMENDATION"] = "EXIT_RECOMMENDATION"
    cycle_timestamp: datetime


# ---------------------------------------------------------------------------
# Core engine
# ---------------------------------------------------------------------------


class ExitScorer:
    """Parallel 100-point exit scorer.

    Each helper accounts for LONG vs SHORT explicitly.
    """

    def __init__(
        self,
        settings: PolarisSettings,
        redis_client: redis_async.Redis,  # type: ignore[type-arg]
        hydra_redis_client: redis_async.Redis,  # type: ignore[type-arg]
        signal_publisher: RedisSignalPublisher,
    ) -> None:
        self._settings = settings
        self._redis = redis_client
        self._hydra = hydra_redis_client
        self._publisher = signal_publisher

    # -- public API --------------------------------------------------------

    async def score_exit(
        self,
        asset: str,
        position_direction: Literal["LONG", "SHORT"],
        entry_score: int,
        raw_data: ExitScoringInputs,
    ) -> ExitSignalOutput:
        """Score a single open position for exit."""
        cycle_ts = datetime.now(timezone.utc)

        breakdown = await self._compute_breakdown(
            asset, position_direction, raw_data,
        )
        total = sum(breakdown.values())
        total = max(0, min(_EXIT_TOTAL, total))

        recommendation = _map_recommendation(total)
        primary = _primary_reason(breakdown)

        output = ExitSignalOutput(
            asset=asset,
            position_direction=position_direction,
            exit_score=total,
            exit_recommendation=recommendation,
            category_breakdown=breakdown,
            primary_exit_reason=primary,
            cycle_timestamp=cycle_ts,
        )

        await self._emit_exit_signal(asset, output)
        return output

    async def score_all_open_positions(
        self,
        open_assets: list[str],
        raw_data_by_asset: dict[str, ExitScoringInputs],
        direction_by_asset: dict[str, Literal["LONG", "SHORT"]] | None = None,
    ) -> list[ExitSignalOutput]:
        """Score all open positions concurrently."""
        dirs = direction_by_asset or {}
        tasks = [
            self._safe_score(asset, raw_data_by_asset.get(asset), dirs.get(asset, "LONG"))
            for asset in open_assets
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return _collect_results(results, open_assets)

    # -- concurrent wrapper ------------------------------------------------

    async def _safe_score(
        self,
        asset: str,
        raw_data: ExitScoringInputs | None,
        direction: Literal["LONG", "SHORT"] = "LONG",
    ) -> ExitSignalOutput | None:
        """Wrap score_exit for safe gather — returns None on error."""
        if raw_data is None:
            logger.warning("missing raw_data for exit scoring | asset={}", asset)
            return None
        return await self.score_exit(asset, direction, 0, raw_data)

    # -- breakdown computation ---------------------------------------------

    async def _compute_breakdown(
        self,
        asset: str,
        direction: Literal["LONG", "SHORT"],
        data: ExitScoringInputs,
    ) -> dict[str, int]:
        """Compute all five category scores."""
        liq = await self._score_liquidation_reversal(direction, asset, data)
        funding = await self._score_funding_reversal(direction, data)
        whale = await self._score_whale_outflow(direction, data)
        oi = await self._score_oi_divergence(direction, data)
        tech = await self._score_technical_deterioration(data)
        return {
            "liquidation_reversal": liq,
            "funding_reversal": funding,
            "whale_outflow": whale,
            "oi_divergence": oi,
            "technical_deterioration": tech,
        }

    # -- category scorers (each ≤ 30 lines) --------------------------------

    async def _score_liquidation_reversal(
        self,
        direction: Literal["LONG", "SHORT"],
        asset: str,
        data: ExitScoringInputs,
    ) -> int:
        """Liquidation reversal — up to 28 pts (leading)."""
        sweep = await self._read_hydra_sweep(asset)
        if sweep is None:
            return 8  # neutral default on HYDRA failure

        sweep_dir = sweep.get("direction", "")
        opposing = _is_opposing_sweep(direction, str(sweep_dir))

        if opposing and sweep.get("active", False):
            return _LIQUIDATION_MAX  # 28

        cluster_pct = data.liquidation_cluster_data.get(
            "nearest_opposing_pct", Decimal("999"),
        )
        if cluster_pct <= Decimal("0.02"):
            return 18
        return 8 if cluster_pct <= Decimal("0.05") else 0

    async def _score_funding_reversal(
        self,
        direction: Literal["LONG", "SHORT"],
        data: ExitScoringInputs,
    ) -> int:
        """Funding rate reversal — up to 25 pts (leading)."""
        curr = data.funding_rate_current
        prev = data.funding_rate_4h_ago
        zscore = data.funding_rate_zscore

        flipped = _funding_flipped(direction, curr, prev)
        if flipped:
            return _FUNDING_MAX  # 25

        trending = _funding_trending_to_flip(direction, curr, zscore)
        if trending:
            return 18

        elevated = _funding_elevated(direction, curr)
        return 10 if elevated else 0

    async def _score_whale_outflow(
        self,
        direction: Literal["LONG", "SHORT"],
        data: ExitScoringInputs,
    ) -> int:
        """Whale outflow — up to 20 pts (leading)."""
        z = data.whale_outflow_zscore
        # Outflow is direction-agnostic in the prompt — high outflow is bad
        if z >= 2.0:
            return _WHALE_MAX  # 20
        if z >= 1.0:
            return 14
        if z >= 0.0:
            return 6
        return 0  # inflow (accumulation)

    async def _score_oi_divergence(
        self,
        direction: Literal["LONG", "SHORT"],
        data: ExitScoringInputs,
    ) -> int:
        """OI divergence — up to 15 pts (leading)."""
        oi = data.oi_change_4h_pct
        price = data.price_change_4h_pct

        diverging = _oi_diverging(direction, oi, price)
        if diverging:
            return _OI_MAX  # 15

        flat_oi = abs(oi) < Decimal("0.005")
        price_moving = _price_moving_with_position(direction, price)
        if flat_oi and price_moving:
            return 10

        same_dir = _oi_same_direction(direction, oi, price)
        return 5 if same_dir else 0

    async def _score_technical_deterioration(
        self,
        data: ExitScoringInputs,
    ) -> int:
        """Technical deterioration — up to 12 pts (lagging)."""
        breaks = data.timeframe_structure_breaks
        rsi_div = data.rsi_divergence_detected

        if breaks >= 2:
            return _TECHNICAL_MAX  # 12
        if breaks == 1:
            return 8
        if rsi_div:
            return 4
        return 0

    # -- signal emission ---------------------------------------------------

    async def _emit_exit_signal(
        self,
        asset: str,
        exit_output: ExitSignalOutput,
    ) -> None:
        """Publish exit recommendation if not HOLD."""
        if exit_output.exit_recommendation == "HOLD":
            return
        try:
            payload = exit_output.model_dump(mode="json")
            await self._publisher.publish(
                f"polaris:signals:{asset}", payload,
            )
            logger.info(
                "exit signal emitted | asset={} | score={} | recommendation={}",
                asset,
                exit_output.exit_score,
                exit_output.exit_recommendation,
            )
        except Exception as exc:
            logger.error(
                "exit signal publish failed | asset={} | err={}",
                asset,
                str(exc),
            )

    # -- HYDRA reader ------------------------------------------------------

    async def _read_hydra_sweep(
        self, asset: str,
    ) -> dict[str, object] | None:
        """Read HYDRA cascade/sweep data for asset."""
        try:
            raw = await asyncio.wait_for(
                self._hydra.get(f"hydra:cpi:{hydra_base_asset(asset)}"), timeout=5.0,
            )
            if raw is None:
                return None
            return msgspec.json.decode(raw)  # type: ignore[return-value]
        except Exception as exc:
            logger.warning(
                "hydra sweep read failed | asset={} | err={}",
                asset,
                str(exc),
            )
            return None


# ---------------------------------------------------------------------------
# Pure helpers — no self, no I/O
# ---------------------------------------------------------------------------


def _map_recommendation(
    score: int,
) -> Literal["EXIT_STRONG", "EXIT_PARTIAL", "EXIT_WATCH", "HOLD"]:
    """Map exit score to recommendation tier."""
    if score >= _STRONG_THRESHOLD:
        return "EXIT_STRONG"
    if score >= _PARTIAL_THRESHOLD:
        return "EXIT_PARTIAL"
    if score >= _WATCH_THRESHOLD:
        return "EXIT_WATCH"
    return "HOLD"


def _primary_reason(breakdown: dict[str, int]) -> str:
    """Return the highest-scoring category name."""
    if not breakdown:
        return "none"
    return max(breakdown, key=breakdown.get)  # type: ignore[arg-type]


def _is_opposing_sweep(
    position_dir: str, sweep_dir: str,
) -> bool:
    """True if sweep direction opposes position direction."""
    if position_dir == "LONG" and sweep_dir in ("SHORT", "short", "bearish"):
        return True
    if position_dir == "SHORT" and sweep_dir in ("LONG", "long", "bullish"):
        return True
    return False


def _funding_flipped(
    direction: str, curr: Decimal, prev: Decimal,
) -> bool:
    """True if funding flipped against position direction."""
    if direction == "LONG":
        return prev > Decimal("0") and curr < Decimal("0")
    return prev < Decimal("0") and curr > Decimal("0")


def _funding_trending_to_flip(
    direction: str, curr: Decimal, zscore: float,
) -> bool:
    """True if funding is trending toward a flip."""
    if direction == "LONG":
        return curr > Decimal("0") and zscore < 0.5
    return curr < Decimal("0") and zscore > -0.5


def _funding_elevated(direction: str, curr: Decimal) -> bool:
    """True if funding is elevated but stable against direction."""
    if direction == "LONG":
        return curr < Decimal("-0.0001")
    return curr > Decimal("0.0001")


def _oi_diverging(
    direction: str, oi_pct: Decimal, price_pct: Decimal,
) -> bool:
    """True if OI is diverging from price (distribution signal)."""
    if direction == "LONG":
        return oi_pct < Decimal("-0.03") and price_pct > Decimal("0")
    return oi_pct < Decimal("-0.03") and price_pct < Decimal("0")


def _price_moving_with_position(
    direction: str, price_pct: Decimal,
) -> bool:
    """True if price is moving in position direction."""
    if direction == "LONG":
        return price_pct > Decimal("0")
    return price_pct < Decimal("0")


def _oi_same_direction(
    direction: str, oi_pct: Decimal, price_pct: Decimal,
) -> bool:
    """True if OI moving same direction as price (not confirming)."""
    if direction == "LONG":
        return oi_pct > Decimal("0") and price_pct > Decimal("0")
    return oi_pct > Decimal("0") and price_pct < Decimal("0")


def _collect_results(
    results: list[ExitSignalOutput | BaseException | None],
    assets: list[str],
) -> list[ExitSignalOutput]:
    """Filter gather results, logging exceptions."""
    outputs: list[ExitSignalOutput] = []
    for asset, result in zip(assets, results):
        if isinstance(result, BaseException):
            logger.error(
                "exit scoring failed for asset | asset={} | err={}",
                asset,
                str(result),
            )
        elif result is not None:
            outputs.append(result)
    return outputs
