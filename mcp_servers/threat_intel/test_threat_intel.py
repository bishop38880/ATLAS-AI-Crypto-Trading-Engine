"""
Test suite for Pre-Execution Threat Intel MCP.

PROMETHEUS Execution Router — Pre-Execution Security Guardrail.
Validates Shadow Delta math, GoPlus flag evaluation, and master
safety verdict logic. All tests are pure unit tests with no
network dependencies.

Sentinel Invariants:
  - pytest only (no unittest)
  - Decimal for tax assertions
  - No mocking of external APIs (tests are logic-only)
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from .models import (
    AssetChange,
    DataStatus,
    ExecutionSafetyReport,
    ShadowDeltaResult,
    TenderlySimulationResult,
    ThreatLevel,
    TokenSecurityFlags,
    TokenSecurityReport,
)
from .security_engine import (
    calculate_shadow_delta,
    evaluate_execution_safety,
    evaluate_goplus_flags,
)


# ──────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────

WALLET: str = "0xABCDef1234567890abcdef1234567890ABCDEF12"


def _make_change(
    to_addr: str,
    amount: str,
    from_addr: str = "0x0000",
) -> AssetChange:
    """Build a test AssetChange."""
    return AssetChange(
        token_address="0xToken",
        from_address=from_addr,
        to_address=to_addr,
        amount=amount,
    )


def _clean_flags() -> TokenSecurityFlags:
    """Build clean (safe) GoPlus flags."""
    return TokenSecurityFlags(
        is_honeypot=False,
        cannot_sell_all=False,
        transfer_pausable=False,
        is_blacklisted=False,
        is_mintable=False,
        buy_tax=Decimal("0.02"),
        sell_tax=Decimal("0.03"),
    )


def _honeypot_flags() -> TokenSecurityFlags:
    """Build honeypot GoPlus flags."""
    return TokenSecurityFlags(is_honeypot=True)


def _high_tax_flags() -> TokenSecurityFlags:
    """Build high-tax GoPlus flags."""
    return TokenSecurityFlags(
        sell_tax=Decimal("0.50"),
        buy_tax=Decimal("0.15"),
    )


def _clean_simulation() -> TenderlySimulationResult:
    """Build a successful Tenderly simulation."""
    return TenderlySimulationResult(
        success=True,
        gas_used=150000,
        asset_changes=[
            _make_change(WALLET, "1000000000000000000"),
        ],
    )


def _failing_simulation() -> TenderlySimulationResult:
    """Build a reverting Tenderly simulation."""
    return TenderlySimulationResult(
        success=False,
        error_message="execution reverted: INSUFFICIENT_OUTPUT",
    )


def _clean_report() -> TokenSecurityReport:
    """Build a clean GoPlus report."""
    return TokenSecurityReport(
        token_address="0xToken",
        chain_id="1",
        flags=_clean_flags(),
        risk_summary="No critical risks detected",
    )


def _honeypot_report() -> TokenSecurityReport:
    """Build a honeypot GoPlus report."""
    return TokenSecurityReport(
        token_address="0xToken",
        chain_id="1",
        flags=_honeypot_flags(),
        risk_summary="CRITICAL: HONEYPOT",
    )


# ──────────────────────────────────────────────────────────────
# Shadow Delta Tests
# ──────────────────────────────────────────────────────────────


class TestShadowDelta:
    """Tests for Shadow Delta calculation."""

    def test_safe_delta_above_threshold(self) -> None:
        """Safe: actual >= 90% of expected."""
        changes = [_make_change(WALLET, "950000000000000000")]
        result: ShadowDeltaResult = calculate_shadow_delta(
            "1000000000000000000", changes, WALLET,
        )
        assert result.is_safe is True
        assert result.delta_pct >= 90.0

    def test_unsafe_delta_hidden_tax(self) -> None:
        """Abort: 99% hidden tax — only 1% received."""
        changes = [_make_change(WALLET, "10000000000000000")]
        result: ShadowDeltaResult = calculate_shadow_delta(
            "1000000000000000000", changes, WALLET,
        )
        assert result.is_safe is False
        assert result.delta_pct < 10.0

    def test_exact_match_is_safe(self) -> None:
        """Exact match (100%) is safe."""
        changes = [_make_change(WALLET, "500000")]
        result: ShadowDeltaResult = calculate_shadow_delta(
            "500000", changes, WALLET,
        )
        assert result.is_safe is True
        assert result.delta_pct == 100.0

    def test_zero_expected_is_unsafe(self) -> None:
        """Zero expected output is always unsafe."""
        result: ShadowDeltaResult = calculate_shadow_delta(
            "0", [], WALLET,
        )
        assert result.is_safe is False

    def test_no_matching_wallet(self) -> None:
        """No credits to our wallet = 0% delta = unsafe."""
        changes = [_make_change("0xOtherWallet", "1000000")]
        result: ShadowDeltaResult = calculate_shadow_delta(
            "1000000", changes, WALLET,
        )
        assert result.is_safe is False
        assert result.delta_pct == 0.0

    def test_multiple_credits_aggregated(self) -> None:
        """Multiple transfers to wallet are summed."""
        changes = [
            _make_change(WALLET, "600000"),
            _make_change(WALLET, "400000"),
        ]
        result: ShadowDeltaResult = calculate_shadow_delta(
            "1000000", changes, WALLET,
        )
        assert result.is_safe is True
        assert result.delta_pct == 100.0


# ──────────────────────────────────────────────────────────────
# GoPlus Flag Tests
# ──────────────────────────────────────────────────────────────


class TestGoPlusFlags:
    """Tests for GoPlus flag evaluation."""

    def test_clean_flags_no_abort(self) -> None:
        """Clean flags produce no abort."""
        abort, reasons = evaluate_goplus_flags(_clean_flags())
        assert abort is False
        assert len(reasons) == 0

    def test_honeypot_triggers_abort(self) -> None:
        """Honeypot flag triggers HARD_ABORT."""
        abort, reasons = evaluate_goplus_flags(_honeypot_flags())
        assert abort is True
        assert any("is_honeypot" in r for r in reasons)

    def test_high_sell_tax_triggers_abort(self) -> None:
        """Sell tax > 10% triggers HARD_ABORT."""
        abort, reasons = evaluate_goplus_flags(_high_tax_flags())
        assert abort is True
        assert any("sell_tax" in r for r in reasons)
        assert any("buy_tax" in r for r in reasons)

    def test_pausable_triggers_abort(self) -> None:
        """Pausable transfer triggers HARD_ABORT."""
        flags = TokenSecurityFlags(transfer_pausable=True)
        abort, reasons = evaluate_goplus_flags(flags)
        assert abort is True
        assert any("transfer_pausable" in r for r in reasons)

    def test_mintable_triggers_abort(self) -> None:
        """Mintable flag triggers HARD_ABORT."""
        flags = TokenSecurityFlags(is_mintable=True)
        abort, reasons = evaluate_goplus_flags(flags)
        assert abort is True
        assert any("is_mintable" in r for r in reasons)

    def test_blacklisted_triggers_abort(self) -> None:
        """Blacklist flag triggers HARD_ABORT."""
        flags = TokenSecurityFlags(is_blacklisted=True)
        abort, reasons = evaluate_goplus_flags(flags)
        assert abort is True
        assert any("is_blacklisted" in r for r in reasons)

    def test_borderline_tax_no_abort(self) -> None:
        """Tax at exactly 10% does not trigger abort."""
        flags = TokenSecurityFlags(
            buy_tax=Decimal("0.10"),
            sell_tax=Decimal("0.10"),
        )
        abort, reasons = evaluate_goplus_flags(flags)
        assert abort is False


# ──────────────────────────────────────────────────────────────
# Master Evaluation Tests
# ──────────────────────────────────────────────────────────────


class TestMasterEvaluation:
    """Tests for the master evaluate_execution_safety."""

    def test_all_clean_no_abort(self) -> None:
        """All layers clean = SAFE, no abort."""
        delta = ShadowDeltaResult(
            expected_wei="1000000",
            actual_wei="950000",
            delta_pct=95.0,
            is_safe=True,
        )
        report: ExecutionSafetyReport = evaluate_execution_safety(
            _clean_simulation(), _clean_report(), delta,
        )
        assert report.hard_abort is False
        assert report.threat_level == ThreatLevel.SAFE

    def test_reverting_sim_triggers_abort(self) -> None:
        """Reverting simulation triggers HARD_ABORT."""
        report: ExecutionSafetyReport = evaluate_execution_safety(
            _failing_simulation(), _clean_report(), None,
        )
        assert report.hard_abort is True
        assert any("REVERTS" in r for r in report.threat_reasons)

    def test_honeypot_triggers_abort(self) -> None:
        """Honeypot in GoPlus triggers HARD_ABORT."""
        delta = ShadowDeltaResult(
            expected_wei="1000", actual_wei="1000",
            delta_pct=100.0, is_safe=True,
        )
        report: ExecutionSafetyReport = evaluate_execution_safety(
            _clean_simulation(), _honeypot_report(), delta,
        )
        assert report.hard_abort is True
        assert any("honeypot" in r for r in report.threat_reasons)

    def test_hidden_tax_triggers_abort(self) -> None:
        """99% hidden tax detected via Shadow Delta."""
        delta = ShadowDeltaResult(
            expected_wei="1000000",
            actual_wei="10000",
            delta_pct=1.0,
            is_safe=False,
        )
        report: ExecutionSafetyReport = evaluate_execution_safety(
            _clean_simulation(), _clean_report(), delta,
        )
        assert report.hard_abort is True
        assert any("Hidden Tax" in r for r in report.threat_reasons)

    def test_sim_unavailable_triggers_abort(self) -> None:
        """Missing simulation triggers HARD_ABORT."""
        report: ExecutionSafetyReport = evaluate_execution_safety(
            None, _clean_report(), None,
        )
        assert report.hard_abort is True
        assert any("unavailable" in r for r in report.threat_reasons)

    def test_goplus_unavailable_triggers_abort(self) -> None:
        """Missing GoPlus triggers HARD_ABORT."""
        report: ExecutionSafetyReport = evaluate_execution_safety(
            _clean_simulation(), None, None,
        )
        assert report.hard_abort is True
        assert any("unavailable" in r for r in report.threat_reasons)

    def test_multiple_threats_critical(self) -> None:
        """Multiple threats = CRITICAL level."""
        delta = ShadowDeltaResult(
            expected_wei="1000", actual_wei="10",
            delta_pct=1.0, is_safe=False,
        )
        report: ExecutionSafetyReport = evaluate_execution_safety(
            _failing_simulation(), _honeypot_report(), delta,
        )
        assert report.hard_abort is True
        assert report.threat_level == ThreatLevel.CRITICAL
        assert len(report.threat_reasons) >= 3
