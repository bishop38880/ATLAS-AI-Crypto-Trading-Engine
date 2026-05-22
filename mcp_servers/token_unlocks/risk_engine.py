"""Risk Engine — Exit Liquidity Guardrail.

ATLAS Intelligence Layer — NewsMacroAgent Integration.

Quantitative logic that cross-references DefiLlama circulating supply
with Dune on-chain unlock schedules to determine whether an impending
cliff unlock threatens to overwhelm the available float.

**Exit Liquidity Guardrail:**
If ``(unlock_amount / circulating_supply) * 100 > threshold_pct``
AND the unlock fires within 72 hours, emit
``conviction_suppression = True`` to veto all pending long positions
for that asset.  This prevents ATLAS from recommending entries that
would be used as exit liquidity by VC and team vesting unlocks.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from loguru import logger

from mcp_servers.token_unlocks.models import (
    DefiLlamaSupply,
    DuneUnlockEvent,
    DuneUnlockSchedule,
    UnlockRiskReport,
)

_WINDOW_HOURS = Decimal("72")
_DEFAULT_THRESHOLD = Decimal("2.0")


def calculate_impact_percentage(
    unlock_amount: Decimal,
    circulating_supply: Decimal,
) -> Decimal:
    """Calculate the percentage impact of an unlock on circulating supply.

    Uses pure ``Decimal`` arithmetic — ``float`` is banned.

    Args:
        unlock_amount: Total tokens scheduled for release.
        circulating_supply: Current circulating token supply.

    Returns:
        ``(unlock_amount / circulating_supply) * 100`` as ``Decimal``,
        or ``Decimal("0")`` if circulating supply is zero.
    """
    if circulating_supply <= Decimal("0"):
        return Decimal("0")
    return (unlock_amount / circulating_supply) * Decimal("100")


def filter_events_within_window(
    events: list[DuneUnlockEvent],
    window_hours: Decimal = _WINDOW_HOURS,
) -> list[DuneUnlockEvent]:
    """Filter unlock events firing within the specified hour window.

    Args:
        events: Full list of ``DuneUnlockEvent`` from the Dune query.
        window_hours: Maximum hours-until-unlock to include (default 72).

    Returns:
        Filtered list containing only imminent events.
    """
    return [
        event for event in events
        if Decimal("0") <= event.hours_until_unlock <= window_hours
    ]


def sum_unlock_amounts(
    events: list[DuneUnlockEvent],
) -> Decimal:
    """Sum ``unlock_amount`` across all events.

    Pure ``Decimal`` accumulation — no ``float``.

    Args:
        events: List of unlock events to aggregate.

    Returns:
        Total token count as ``Decimal``.
    """
    total = Decimal("0")
    for event in events:
        total += event.unlock_amount
    return total


def determine_risk_tier(
    impact_pct: Decimal,
    threshold_pct: Decimal,
) -> str:
    """Classify unlock risk into tiers.

    Args:
        impact_pct: Calculated impact as ``Decimal``.
        threshold_pct: Configured suppression threshold.

    Returns:
        ``"CRITICAL"`` if above threshold, ``"ELEVATED"`` if above
        half-threshold, or ``"NORMAL"`` otherwise.
    """
    if impact_pct >= threshold_pct:
        return "CRITICAL"
    if impact_pct >= threshold_pct / Decimal("2"):
        return "ELEVATED"
    return "NORMAL"


def evaluate_exit_liquidity(
    supply: DefiLlamaSupply,
    schedule: DuneUnlockSchedule,
    threshold_pct: Decimal = _DEFAULT_THRESHOLD,
) -> UnlockRiskReport:
    """Master evaluation combining Layer 1 + Layer 2 data.

    ATLAS Intelligence Layer — NewsMacroAgent.
    Exit Liquidity Guardrail: filter → sum → impact → suppress.

    Args:
        supply: DefiLlama circulating/total supply data.
        schedule: Dune on-chain unlock schedule.
        threshold_pct: Suppression threshold (default ``2.0%``).
    """
    imminent_events = filter_events_within_window(schedule.events)
    total_unlock_72h = sum_unlock_amounts(imminent_events)
    impact = calculate_impact_percentage(
        total_unlock_72h, supply.circulating_supply,
    )
    risk_tier = determine_risk_tier(impact, threshold_pct)
    suppress = impact > threshold_pct and len(imminent_events) > 0

    _log_evaluation(
        supply.symbol, impact, threshold_pct, suppress,
        len(imminent_events),
    )

    return _build_risk_report(
        supply, schedule, threshold_pct,
        impact, total_unlock_72h, len(imminent_events),
        risk_tier, suppress,
    )


def _build_risk_report(
    supply: DefiLlamaSupply,
    schedule: DuneUnlockSchedule,
    threshold_pct: Decimal,
    impact: Decimal,
    total_unlock_72h: Decimal,
    event_count: int,
    risk_tier: str,
    suppress: bool,
) -> UnlockRiskReport:
    """Construct the ``UnlockRiskReport`` struct."""
    now_utc = datetime.now(timezone.utc).isoformat()
    return UnlockRiskReport(
        symbol=supply.symbol,
        conviction_suppression=suppress,
        impact_pct=impact,
        threshold_pct=threshold_pct,
        circulating_supply=supply.circulating_supply,
        total_unlock_amount_72h=total_unlock_72h,
        unlock_events_72h=event_count,
        risk_tier=risk_tier,
        supply_data=supply,
        unlock_schedule=schedule,
        evaluated_at=now_utc,
    )


def _log_evaluation(
    symbol: str,
    impact: Decimal,
    threshold: Decimal,
    suppress: bool,
    event_count: int,
) -> None:
    """Structured log for the risk evaluation result."""
    if suppress:
        logger.warning(
            "EXIT LIQUIDITY GUARDRAIL TRIGGERED | symbol={} | "
            "impact_pct={} | threshold={} | events_72h={}",
            symbol, impact, threshold, event_count,
        )
    else:
        logger.info(
            "Unlock risk evaluated | symbol={} | impact_pct={} | "
            "threshold={} | conviction_suppression={}",
            symbol, impact, threshold, suppress,
        )
