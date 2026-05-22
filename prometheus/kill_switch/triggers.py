"""Kill switch trigger functions — pure functions returning halt decisions.

Each trigger returns ``(should_halt, reason, details)`` where:
    - ``should_halt``: ``True`` if the trigger condition is met.
    - ``reason``: Machine-readable reason code matching ``SystemHaltEvent.reason``.
    - ``details``: Arbitrary metadata about the trigger condition.

Integration: a background task evaluates all triggers every 500 ms and
calls ``kill_switch.halt(...)`` if any returns ``should_halt=True``.
"""

from decimal import Decimal

from prometheus.kill_switch.retry_budget import RetryBudget

# Type alias for trigger return signature.
TriggerResult = tuple[bool, str, dict[str, str | int | float]]


def spike_detector_trigger(
    pnl_history: list[Decimal],
    threshold: Decimal = Decimal("-0.03"),
) -> TriggerResult:
    """Halt if rolling 1-minute P&L breaches the threshold.

    Args:
        pnl_history: Recent P&L values (most recent last).
        threshold: Minimum acceptable rolling P&L (default -3%).

    Returns:
        Trigger result tuple.
    """
    if not pnl_history:
        return False, "SPIKE_DETECTOR", {}
    rolling_pnl = sum(pnl_history)
    if rolling_pnl < threshold:
        return True, "SPIKE_DETECTOR", {
            "rolling_pnl": str(rolling_pnl),
            "threshold": str(threshold),
            "window_size": len(pnl_history),
        }
    return False, "SPIKE_DETECTOR", {}


def position_limit_trigger(
    positions: list[dict[str, object]],
    max_positions: int,
) -> TriggerResult:
    """Halt if the number of open positions exceeds the maximum.

    Args:
        positions: List of open position dicts.
        max_positions: Maximum allowed concurrent positions.

    Returns:
        Trigger result tuple.
    """
    count = len(positions)
    if count > max_positions:
        return True, "POSITION_LIMIT_BREACH", {
            "open_positions": count,
            "max_allowed": max_positions,
        }
    return False, "POSITION_LIMIT_BREACH", {}


def api_failure_storm_trigger(
    retry_budget: RetryBudget,
) -> TriggerResult:
    """Halt if the retry budget has been exhausted.

    Args:
        retry_budget: Active retry budget tracker.

    Returns:
        Trigger result tuple.
    """
    if retry_budget.budget_exhausted():
        return True, "API_FAILURE_STORM", {
            "elapsed_seconds": retry_budget._elapsed,
            "budget_seconds": retry_budget.total_budget_seconds,
        }
    return False, "API_FAILURE_STORM", {}


def liquidation_proximity_trigger(
    positions: list[dict[str, Decimal]],
    threshold_pct: Decimal = Decimal("0.85"),
) -> TriggerResult:
    """Halt if any position is within proximity of liquidation price.

    A position is considered at risk if
    ``current_price / liquidation_price >= threshold_pct``.

    Args:
        positions: List of position dicts with ``current_price`` and
            ``liquidation_price`` keys (both ``Decimal``).
        threshold_pct: Proximity threshold (default 0.85 = within 15%).

    Returns:
        Trigger result tuple.
    """
    for pos in positions:
        liq_price = pos.get("liquidation_price", Decimal("0"))
        cur_price = pos.get("current_price", Decimal("0"))
        if liq_price <= Decimal("0"):
            continue
        ratio = cur_price / liq_price
        if ratio >= threshold_pct:
            return True, "LIQUIDATION_PROXIMITY", {
                "current_price": str(cur_price),
                "liquidation_price": str(liq_price),
                "ratio": str(ratio),
                "threshold": str(threshold_pct),
            }
    return False, "LIQUIDATION_PROXIMITY", {}
