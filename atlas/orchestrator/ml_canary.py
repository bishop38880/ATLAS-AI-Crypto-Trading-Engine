"""ML Canary Orchestrator — shadow comparison and promotion engine.

Manages the canary deployment lifecycle for ML models:
    1. Shadow comparison — run new models in parallel with live.
    2. Deterministic cycle selection — hash-based 10%/50% routing.
    3. Canary evaluation — promote or rollback based on performance.

Architecture note:
    Canary deployments only affect analysis cycles, never trades.
    Rollback is automatic and completes within the next evaluation
    cycle (<5 minutes in production).
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

from loguru import logger

from atlas.ml.model_registry import CanaryState, ModelRegistry
from atlas.shared.config import CanaryConfig


# ---------------------------------------------------------------------------
# Deterministic cycle selection
# ---------------------------------------------------------------------------


def deterministic_canary_hash(
    asset: str,
    timestamp: datetime,
    threshold_pct: int,
) -> bool:
    """Determine if this cycle should use the canary model.

    Uses SHA-256 of ``asset + timestamp_bucket`` for uniform,
    reproducible distribution. The timestamp is bucketed to the
    minute to ensure consistency within a cycle.

    Args:
        asset: Trading pair (e.g. ``BTCUSDT``).
        timestamp: Cycle timestamp.
        threshold_pct: Percentage threshold (10 or 50).

    Returns:
        True if this cycle should use the canary model.
    """
    bucket = timestamp.strftime("%Y%m%d%H%M")
    raw = "{}:{}".format(asset, bucket)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    hash_value = int(digest[:8], 16) % 100
    return hash_value < threshold_pct


# ---------------------------------------------------------------------------
# Performance helpers
# ---------------------------------------------------------------------------


def compute_rolling_sharpe(
    returns: list[float],
    window_hours: int = 4,
    cycles_per_hour: int = 2,
) -> float:
    """Compute annualised Sharpe ratio over a rolling window.

    Args:
        returns: List of per-cycle returns.
        window_hours: Rolling window in hours.
        cycles_per_hour: Number of analysis cycles per hour.

    Returns:
        Annualised Sharpe ratio (0.0 if insufficient data).
    """
    window_size = window_hours * cycles_per_hour
    if len(returns) < max(window_size, 2):
        return 0.0

    window = returns[-window_size:]
    mean_ret = sum(window) / len(window)
    variance = sum((r - mean_ret) ** 2 for r in window) / len(window)
    std_ret = variance ** 0.5

    if std_ret < 1e-10:
        return 0.0

    periods_per_year = cycles_per_hour * 24 * 365
    return (mean_ret / std_ret) * (periods_per_year ** 0.5)


def compute_max_drawdown(returns: list[float]) -> float:
    """Compute maximum drawdown from a series of returns.

    Args:
        returns: List of per-cycle returns.

    Returns:
        Maximum drawdown as a positive fraction (0.0 if no drawdown).
    """
    if not returns:
        return 0.0

    cumulative = _build_cumulative(returns)
    peak = cumulative[0]
    max_dd = 0.0

    for val in cumulative[1:]:
        if val > peak:
            peak = val
        drawdown = (peak - val) / peak if peak > 0 else 0.0
        if drawdown > max_dd:
            max_dd = drawdown

    return max_dd


def _build_cumulative(returns: list[float]) -> list[float]:
    """Convert returns to cumulative equity curve."""
    equity = [1.0]
    for r in returns:
        equity.append(equity[-1] * (1.0 + r))
    return equity


def should_rollback(
    canary_sharpe: float,
    live_sharpe: float,
    canary_dd: float,
    live_dd: float,
    tolerance_pct: float = 0.10,
) -> bool:
    """Determine if canary model should be rolled back.

    Rollback triggers:
        - Canary Sharpe degrades >tolerance relative to live.
        - Canary max drawdown exceeds live by >tolerance.

    Args:
        canary_sharpe: Canary model's rolling Sharpe.
        live_sharpe: Live model's rolling Sharpe.
        canary_dd: Canary model's max drawdown.
        live_dd: Live model's max drawdown.
        tolerance_pct: Degradation tolerance (default 10%).

    Returns:
        True if canary should be rolled back.
    """
    sharpe_degraded = _sharpe_is_degraded(
        canary_sharpe, live_sharpe, tolerance_pct,
    )
    drawdown_degraded = _drawdown_is_degraded(
        canary_dd, live_dd, tolerance_pct,
    )
    return sharpe_degraded or drawdown_degraded


def _sharpe_is_degraded(
    canary: float, live: float, tolerance: float,
) -> bool:
    """Check if canary Sharpe is degraded beyond tolerance."""
    if live <= 0:
        return canary < live
    return (live - canary) / abs(live) > tolerance


def _drawdown_is_degraded(
    canary_dd: float, live_dd: float, tolerance: float,
) -> bool:
    """Check if canary drawdown exceeds live by tolerance."""
    if live_dd < 1e-10:
        return canary_dd > tolerance
    return (canary_dd - live_dd) / live_dd > tolerance


# ---------------------------------------------------------------------------
# Shadow comparison
# ---------------------------------------------------------------------------


def compare_shadow_outputs(
    live_output: Any,
    shadow_output: Any,
    task_type: str,
    agreement_threshold: float = 0.95,
    mae_tolerance: float = 0.10,
) -> bool:
    """Compare shadow model output against live model output.

    Args:
        live_output: Output from the live model.
        shadow_output: Output from the shadow model.
        task_type: ``classification`` or ``regression``.
        agreement_threshold: Required agreement for classification.
        mae_tolerance: MAE tolerance for regression (fraction of live).

    Returns:
        True if shadow output agrees with live within tolerance.
    """
    if task_type == "classification":
        return _compare_classification(
            live_output, shadow_output, agreement_threshold,
        )
    return _compare_regression(
        live_output, shadow_output, mae_tolerance,
    )


def _compare_classification(
    live: Any, shadow: Any, threshold: float,
) -> bool:
    """Compare classification outputs for agreement."""
    if isinstance(live, list) and isinstance(shadow, list):
        if len(live) == 0:
            return True
        agreements = sum(
            1 for a, b in zip(live, shadow) if a == b
        )
        return agreements / len(live) >= threshold

    return live == shadow


def _compare_regression(
    live: Any, shadow: Any, tolerance: float,
) -> bool:
    """Compare regression outputs within MAE tolerance."""
    if isinstance(live, (int, float)) and isinstance(shadow, (int, float)):
        if abs(live) < 1e-10:
            return abs(shadow) < tolerance
        mae = abs(live - shadow) / abs(live)
        return mae <= tolerance

    if isinstance(live, list) and isinstance(shadow, list):
        if len(live) == 0:
            return True
        total_error = sum(
            abs(a - b) for a, b in zip(live, shadow)
        )
        mean_live = sum(abs(v) for v in live) / len(live)
        if mean_live < 1e-10:
            return total_error / len(live) < tolerance
        mae = (total_error / len(live)) / mean_live
        return mae <= tolerance

    return False


# ---------------------------------------------------------------------------
# Canary Orchestrator
# ---------------------------------------------------------------------------


class CanaryOrchestrator:
    """Orchestrates the ML canary deployment lifecycle.

    Attributes:
        _registry: ModelRegistry for state persistence.
        _config: CanaryConfig for thresholds and timing.
        _canary_returns: In-memory return tracking per model.
        _live_returns: In-memory return tracking for live models.
    """

    def __init__(
        self,
        registry: ModelRegistry,
        config: CanaryConfig,
    ) -> None:
        """Initialize with registry and configuration.

        Args:
            registry: PostgreSQL-backed model registry.
            config: Canary deployment configuration.
        """
        self._registry = registry
        self._config = config
        self._canary_returns: dict[str, list[float]] = {}
        self._live_returns: dict[str, list[float]] = {}
        self._shadow_agreements: dict[str, list[bool]] = {}

    async def run_shadow_cycle(
        self,
        model_name: str,
        version: str,
        live_output: Any,
        shadow_output: Any,
        task_type: str = "classification",
    ) -> CanaryState:
        """Process one shadow comparison cycle for a model.

        Args:
            model_name: Model identifier.
            version: Model version.
            live_output: Output from the live model.
            shadow_output: Output from the shadow model.
            task_type: ``classification`` or ``regression``.

        Returns:
            Current canary state after this cycle.
        """
        key = "{}:{}".format(model_name, version)
        agrees = self._record_shadow_agreement(
            key, live_output, shadow_output, task_type,
        )
        remaining = await self._registry.decrement_shadow_cycles(
            model_name, version,
        )

        if remaining <= 0 and self._check_shadow_agreement(key):
            await self._promote_from_shadow(model_name, version)
            return CanaryState.CANARY_10PCT

        _log_canary_event(
            "shadow_cycle", model_name, version,
            {"agrees": agrees, "remaining": remaining},
        )
        return CanaryState.SHADOW

    def _record_shadow_agreement(
        self,
        key: str,
        live_output: Any,
        shadow_output: Any,
        task_type: str,
    ) -> bool:
        """Compare outputs and append to agreement history."""
        agrees = compare_shadow_outputs(
            live_output, shadow_output, task_type,
            self._config.classification_agreement_threshold,
            self._config.regression_mae_tolerance,
        )
        self._shadow_agreements.setdefault(key, [])
        self._shadow_agreements[key].append(agrees)
        return agrees

    def _check_shadow_agreement(self, key: str) -> bool:
        """Check if shadow agreement rate meets threshold."""
        history = self._shadow_agreements.get(key, [])
        if not history:
            return False
        agreement_rate = sum(1 for a in history if a) / len(history)
        return agreement_rate >= self._config.classification_agreement_threshold

    async def _promote_from_shadow(
        self, model_name: str, version: str,
    ) -> None:
        """Promote a model from shadow to canary_10pct."""
        await self._registry.update_state(
            model_name, version, CanaryState.CANARY_10PCT,
        )
        _log_canary_event(
            "promoted_to_canary_10pct",
            model_name, version, {},
        )

    def should_use_canary(
        self,
        state: CanaryState,
        asset: str,
        timestamp: datetime,
    ) -> bool:
        """Determine if this cycle should use the canary model.

        Args:
            state: Current canary state.
            asset: Trading pair.
            timestamp: Cycle timestamp.

        Returns:
            True if canary model should be used for this cycle.
        """
        if state == CanaryState.CANARY_10PCT:
            return deterministic_canary_hash(asset, timestamp, 10)
        if state == CanaryState.CANARY_50PCT:
            return deterministic_canary_hash(asset, timestamp, 50)
        return False

    async def record_cycle_return(
        self,
        model_name: str,
        version: str,
        canary_return: float,
        live_return: float,
    ) -> None:
        """Record per-cycle returns for canary and live models.

        Args:
            model_name: Model identifier.
            version: Model version.
            canary_return: Return from canary model.
            live_return: Return from live model.
        """
        key = "{}:{}".format(model_name, version)
        self._canary_returns.setdefault(key, [])
        self._live_returns.setdefault(key, [])
        self._canary_returns[key].append(canary_return)
        self._live_returns[key].append(live_return)

    async def evaluate_canary(
        self,
        model_name: str,
        version: str,
    ) -> CanaryState:
        """Evaluate canary performance and promote or rollback.

        Checks rolling Sharpe and max drawdown against live.
        Promotes after duration threshold; rolls back on degradation.

        Args:
            model_name: Model identifier.
            version: Model version.

        Returns:
            New canary state after evaluation.
        """
        entry = await self._registry.get_model(model_name, version)
        if entry is None:
            return CanaryState.SHADOW

        key = "{}:{}".format(model_name, version)
        canary_rets = self._canary_returns.get(key, [])
        live_rets = self._live_returns.get(key, [])

        if len(canary_rets) < 2 or len(live_rets) < 2:
            return entry.canary_state

        return await self._evaluate_and_transition(
            entry, canary_rets, live_rets,
        )

    async def _evaluate_and_transition(
        self,
        entry: Any,
        canary_rets: list[float],
        live_rets: list[float],
    ) -> CanaryState:
        """Core evaluation: rollback or promote."""
        canary_sharpe = compute_rolling_sharpe(
            canary_rets, self._config.rolling_window_hours,
        )
        live_sharpe = compute_rolling_sharpe(
            live_rets, self._config.rolling_window_hours,
        )
        canary_dd = compute_max_drawdown(canary_rets)
        live_dd = compute_max_drawdown(live_rets)

        if should_rollback(
            canary_sharpe, live_sharpe, canary_dd, live_dd,
            self._config.canary_sharpe_degradation_pct,
        ):
            return await self._rollback(entry)

        return await self._check_promotion(entry)

    async def _rollback(self, entry: Any) -> CanaryState:
        """Rollback a canary model to shadow state."""
        await self._registry.update_state(
            entry.model_name, entry.version, CanaryState.SHADOW,
        )
        _log_canary_event(
            "rollback",
            entry.model_name, entry.version,
            {"from_state": entry.canary_state.value},
        )
        key = "{}:{}".format(entry.model_name, entry.version)
        self._canary_returns.pop(key, None)
        self._live_returns.pop(key, None)
        return CanaryState.SHADOW

    async def _check_promotion(self, entry: Any) -> CanaryState:
        """Check if a canary model should be promoted."""
        if entry.canary_start_time is None:
            return entry.canary_state

        now = datetime.now(timezone.utc)
        elapsed = now - entry.canary_start_time

        if entry.canary_state == CanaryState.CANARY_10PCT:
            return await self._try_promote_10_to_50(
                entry, elapsed,
            )
        if entry.canary_state == CanaryState.CANARY_50PCT:
            return await self._try_promote_50_to_live(
                entry, elapsed,
            )
        return entry.canary_state

    async def _try_promote_10_to_50(
        self, entry: Any, elapsed: timedelta,
    ) -> CanaryState:
        """Promote from canary_10pct to canary_50pct after duration."""
        threshold = timedelta(days=self._config.canary_10pct_duration_days)
        if elapsed >= threshold:
            await self._registry.update_state(
                entry.model_name, entry.version,
                CanaryState.CANARY_50PCT,
            )
            _log_canary_event(
                "promoted_to_canary_50pct",
                entry.model_name, entry.version,
                {"elapsed_days": elapsed.days},
            )
            return CanaryState.CANARY_50PCT
        return CanaryState.CANARY_10PCT

    async def _try_promote_50_to_live(
        self, entry: Any, elapsed: timedelta,
    ) -> CanaryState:
        """Promote from canary_50pct to live after duration."""
        threshold = timedelta(days=self._config.canary_50pct_duration_days)
        if elapsed >= threshold:
            await self._retire_current_live(entry.model_name)
            await self._registry.update_state(
                entry.model_name, entry.version,
                CanaryState.LIVE,
            )
            _log_canary_event(
                "promoted_to_live",
                entry.model_name, entry.version,
                {"elapsed_days": elapsed.days},
            )
            return CanaryState.LIVE
        return CanaryState.CANARY_50PCT

    async def _retire_current_live(self, model_name: str) -> None:
        """Retire the current live model when a new one is promoted."""
        current_live = await self._registry.get_live_model(model_name)
        if current_live is not None:
            await self._registry.update_state(
                current_live.model_name,
                current_live.version,
                CanaryState.RETIRED,
            )
            _log_canary_event(
                "retired",
                current_live.model_name,
                current_live.version,
                {},
            )

    async def evaluate_all_canaries(self) -> dict[str, CanaryState]:
        """Evaluate all models currently in canary states.

        Returns:
            Mapping of ``model_name:version`` to new state.
        """
        results: dict[str, CanaryState] = {}

        for state in (CanaryState.CANARY_10PCT, CanaryState.CANARY_50PCT):
            models = await self._registry.get_models_by_state(state)
            for model in models:
                new_state = await self.evaluate_canary(
                    model.model_name, model.version,
                )
                key = "{}:{}".format(model.model_name, model.version)
                results[key] = new_state

        return results


# ---------------------------------------------------------------------------
# Logging helper
# ---------------------------------------------------------------------------


def _log_canary_event(
    event_type: str,
    model_name: str,
    version: str,
    details: dict[str, Any],
) -> None:
    """Emit a structured canary lifecycle log event."""
    logger.info(
        "canary_{} | model={} | version={} | details={}",
        event_type, model_name, version, details,
    )
