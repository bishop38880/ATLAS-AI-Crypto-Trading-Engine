"""
Pre-execution security evaluation engine.

PROMETHEUS Execution Router — Pre-Execution Security Guardrail.
Contains the quantitative logic for:
  1. Shadow Delta calculation (actual vs expected token receipt)
  2. GoPlus flag evaluation (honeypot, tax, pausable, etc.)
  3. Master safety verdict combining both layers

Sentinel Invariants:
  - Decimal for tax thresholds
  - int arithmetic for wei (never float)
  - Max 40 lines per function
  - Loguru structured kwargs
"""

from __future__ import annotations

from decimal import Decimal

from loguru import logger

from .models import (
    AssetChange,
    ExecutionSafetyReport,
    ShadowDeltaResult,
    TenderlySimulationResult,
    ThreatLevel,
    TokenSecurityFlags,
    TokenSecurityReport,
)

# ──────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────

_DEFAULT_DELTA_THRESHOLD: float = 90.0
_TAX_THRESHOLD: Decimal = Decimal("0.10")


# ──────────────────────────────────────────────────────────────
# Shadow Delta Calculation
# ──────────────────────────────────────────────────────────────


def calculate_shadow_delta(
    expected_output_wei: str,
    asset_changes: list[AssetChange],
    wallet_address: str,
    threshold_pct: float = _DEFAULT_DELTA_THRESHOLD,
) -> ShadowDeltaResult:
    """
    Calculate Shadow Delta: (actual / expected) * 100.

    PROMETHEUS Execution Router — Pre-Execution Security Guardrail.
    Below threshold indicates hidden tax or slippage trap.
    """
    expected = int(expected_output_wei)
    if expected <= 0:
        return _zero_delta_result(expected_output_wei, threshold_pct)

    actual = _find_actual_receipt(asset_changes, wallet_address)
    delta_pct = (actual / expected) * 100.0

    logger.info(
        "Shadow Delta | expected={} | actual={} | delta={}%",
        expected, actual, round(delta_pct, 2),
    )

    return ShadowDeltaResult(
        expected_wei=expected_output_wei,
        actual_wei=str(actual),
        delta_pct=round(delta_pct, 4),
        is_safe=delta_pct >= threshold_pct,
        threshold_pct=threshold_pct,
    )


def _find_actual_receipt(
    asset_changes: list[AssetChange],
    wallet_address: str,
) -> int:
    """
    Find the total tokens credited to our wallet.

    Args:
        asset_changes: List of Tenderly asset changes.
        wallet_address: Our wallet address (case-insensitive).

    Returns:
        Total wei received by our wallet.
    """
    wallet_lower = wallet_address.lower()
    total: int = 0
    for change in asset_changes:
        if change.to_address.lower() == wallet_lower:
            total += int(change.amount)
    return total


def _zero_delta_result(
    expected_wei: str, threshold: float,
) -> ShadowDeltaResult:
    """Build a zero-delta result when expected is zero."""
    return ShadowDeltaResult(
        expected_wei=expected_wei,
        actual_wei="0",
        delta_pct=0.0,
        is_safe=False,
        threshold_pct=threshold,
    )


# ──────────────────────────────────────────────────────────────
# GoPlus Flag Evaluation
# ──────────────────────────────────────────────────────────────


def evaluate_goplus_flags(
    flags: TokenSecurityFlags,
) -> tuple[bool, list[str]]:
    """
    Evaluate GoPlus security flags for HARD_ABORT triggers.

    PROMETHEUS Execution Router — Pre-Execution Security Guardrail.
    If ANY critical flag is True or tax exceeds 10%, returns
    (True, [reasons]) indicating HARD_ABORT is required.

    Args:
        flags: Parsed GoPlus token security flags.

    Returns:
        Tuple of (should_abort, list_of_threat_reasons).
    """
    reasons: list[str] = []
    _check_boolean_flags(flags, reasons)
    _check_tax_thresholds(flags, reasons)
    return (len(reasons) > 0, reasons)


def _check_boolean_flags(
    flags: TokenSecurityFlags,
    reasons: list[str],
) -> None:
    """Check critical boolean flags and append reasons."""
    checks: list[tuple[bool, str]] = [
        (flags.is_honeypot, "GoPlus: is_honeypot=True"),
        (flags.cannot_sell_all, "GoPlus: cannot_sell_all=True"),
        (flags.transfer_pausable, "GoPlus: transfer_pausable=True"),
        (flags.is_blacklisted, "GoPlus: is_blacklisted=True"),
        (flags.is_mintable, "GoPlus: is_mintable=True"),
    ]
    for flag_value, reason in checks:
        if flag_value:
            reasons.append(reason)


def _check_tax_thresholds(
    flags: TokenSecurityFlags,
    reasons: list[str],
) -> None:
    """Check buy/sell tax against the 10% threshold."""
    if flags.buy_tax > _TAX_THRESHOLD:
        reasons.append(
            f"GoPlus: buy_tax={flags.buy_tax} exceeds 10% threshold",
        )
    if flags.sell_tax > _TAX_THRESHOLD:
        reasons.append(
            f"GoPlus: sell_tax={flags.sell_tax} exceeds 10% threshold",
        )


# ──────────────────────────────────────────────────────────────
# Master Safety Evaluation
# ──────────────────────────────────────────────────────────────


def evaluate_execution_safety(
    simulation: TenderlySimulationResult | None,
    security_report: TokenSecurityReport | None,
    shadow_delta: ShadowDeltaResult | None,
) -> ExecutionSafetyReport:
    """
    Produce the master pre-execution safety verdict.

    PROMETHEUS Execution Router — Pre-Execution Security Guardrail.
    Combines Tenderly simulation, GoPlus flags, and Shadow Delta
    into a single HARD_ABORT decision.

    Args:
        simulation: Tenderly result (None if API failed).
        security_report: GoPlus report (None if API failed).
        shadow_delta: Shadow Delta result (None if sim failed).

    Returns:
        ExecutionSafetyReport with hard_abort and threat_reasons.
    """
    reasons: list[str] = []
    _evaluate_simulation(simulation, reasons)
    _evaluate_security(security_report, reasons)
    _evaluate_delta(shadow_delta, reasons)

    hard_abort = len(reasons) > 0
    threat_level = _classify_threat(hard_abort, reasons)

    return ExecutionSafetyReport(
        hard_abort=hard_abort,
        threat_level=threat_level,
        threat_reasons=reasons,
        simulation=simulation,
        security_report=security_report,
        shadow_delta=shadow_delta,
    )


def _evaluate_simulation(
    sim: TenderlySimulationResult | None,
    reasons: list[str],
) -> None:
    """Check simulation result for abort triggers."""
    if sim is None:
        reasons.append("Tenderly: Simulation unavailable (API failure)")
        return
    if not sim.success:
        reasons.append(
            f"Tenderly: Transaction REVERTS — {sim.error_message}",
        )


def _evaluate_security(
    report: TokenSecurityReport | None,
    reasons: list[str],
) -> None:
    """Check GoPlus report for abort triggers."""
    if report is None:
        reasons.append("GoPlus: Security scan unavailable (API failure)")
        return
    _, flag_reasons = evaluate_goplus_flags(report.flags)
    reasons.extend(flag_reasons)


def _evaluate_delta(
    delta: ShadowDeltaResult | None,
    reasons: list[str],
) -> None:
    """Check Shadow Delta for hidden tax abort."""
    if delta is None:
        return
    if not delta.is_safe:
        tax_pct = round(100.0 - delta.delta_pct, 2)
        reasons.append(
            f"Tenderly: {tax_pct}% Hidden Tax Detected "
            f"(Shadow Delta: {delta.delta_pct}%)",
        )


def _classify_threat(
    hard_abort: bool,
    reasons: list[str],
) -> ThreatLevel:
    """Classify the overall threat level."""
    if not hard_abort:
        return ThreatLevel.SAFE
    if len(reasons) >= 3:
        return ThreatLevel.CRITICAL
    return ThreatLevel.CRITICAL
