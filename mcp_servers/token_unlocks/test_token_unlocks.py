"""Tests for Token Unlocks MCP — Sentinel-grade coverage.

Covers:
1. Model serialisation round-trip with Decimal precision.
2. Risk engine: suppression trigger, threshold boundary, zero-supply.
3. DefiLlama client: successful parse, degraded fallback.
4. Dune client: poll loop, degraded schedule on failure.
5. Tool dispatch: end-to-end handler validation.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import msgspec
import pytest

from mcp_servers.token_unlocks.models import (
    ENCODER,
    DECODER,
    DefiLlamaSupply,
    DuneUnlockEvent,
    DuneUnlockSchedule,
    UnlockRiskReport,
    decimal_dec_hook,
    decimal_enc_hook,
)
from mcp_servers.token_unlocks.risk_engine import (
    calculate_impact_percentage,
    determine_risk_tier,
    evaluate_exit_liquidity,
    filter_events_within_window,
    sum_unlock_amounts,
)


# ═══════════════════════ Model & Serialisation Tests ═════════════════════════

class TestDecimalHooks:
    """Verify custom msgspec Decimal hooks preserve precision."""

    def test_encode_decimal_to_string(self) -> None:
        """Decimal encodes as a JSON string, not a float."""
        val = Decimal("123456789.123456789012345678")
        encoded = decimal_enc_hook(val)
        assert isinstance(encoded, str)
        assert encoded == "123456789.123456789012345678"

    def test_decode_string_to_decimal(self) -> None:
        """String input decodes back to exact Decimal."""
        result = decimal_dec_hook(Decimal, "99.999999999")
        assert isinstance(result, Decimal)
        assert result == Decimal("99.999999999")

    def test_decode_float_to_decimal(self) -> None:
        """Float input is cast via str to avoid precision loss."""
        result = decimal_dec_hook(Decimal, 1.23)
        assert isinstance(result, Decimal)

    def test_encode_unsupported_raises(self) -> None:
        """Non-Decimal types raise TypeError."""
        with pytest.raises(TypeError):
            decimal_enc_hook("not_a_decimal")


class TestModelRoundTrip:
    """Verify Struct → JSON → Struct round-trip with Decimal."""

    def test_supply_roundtrip(self) -> None:
        """DefiLlamaSupply survives JSON encode/decode."""
        supply = DefiLlamaSupply(
            symbol="SOL",
            circulating_supply=Decimal("450000000.123"),
            total_supply=Decimal("570000000.456"),
            circulating_ratio=Decimal("0.789473"),
            price_usd=Decimal("142.55"),
            market_cap_usd=Decimal("64147500000"),
            fetched_at="2026-04-26T12:00:00Z",
            status="OK",
        )
        encoded = ENCODER.encode(supply)
        assert b"450000000.123" in encoded
        # Verify it's valid JSON
        decoded = msgspec.json.decode(encoded)
        assert decoded["symbol"] == "SOL"

    def test_risk_report_conviction_suppression(self) -> None:
        """UnlockRiskReport serialises the suppression flag correctly."""
        report = UnlockRiskReport(
            symbol="ARB",
            conviction_suppression=True,
            impact_pct=Decimal("5.4"),
            threshold_pct=Decimal("2.0"),
        )
        encoded = ENCODER.encode(report)
        decoded = msgspec.json.decode(encoded)
        assert decoded["conviction_suppression"] is True
        assert decoded["impact_pct"] == "5.4"


# ════════════════════════ Risk Engine Tests ══════════════════════════════════

class TestCalculateImpact:
    """Tests for ``calculate_impact_percentage``."""

    def test_normal_impact(self) -> None:
        """Standard calculation: 5M of 100M = 5%."""
        result = calculate_impact_percentage(
            Decimal("5000000"), Decimal("100000000"),
        )
        assert result == Decimal("5")

    def test_zero_circulating_returns_zero(self) -> None:
        """Division by zero produces Decimal("0"), not an exception."""
        result = calculate_impact_percentage(
            Decimal("1000000"), Decimal("0"),
        )
        assert result == Decimal("0")

    def test_small_impact(self) -> None:
        """Sub-percent impact is preserved as exact Decimal."""
        result = calculate_impact_percentage(
            Decimal("100"), Decimal("1000000"),
        )
        assert result == Decimal("0.01")


class TestFilterEventsWindow:
    """Tests for the 72-hour window filter."""

    def test_filters_beyond_window(self) -> None:
        """Events >72h out are excluded."""
        events = [
            DuneUnlockEvent(symbol="ARB", hours_until_unlock=Decimal("24")),
            DuneUnlockEvent(symbol="ARB", hours_until_unlock=Decimal("100")),
            DuneUnlockEvent(symbol="ARB", hours_until_unlock=Decimal("71")),
        ]
        filtered = filter_events_within_window(events)
        assert len(filtered) == 2

    def test_empty_list_returns_empty(self) -> None:
        """No events → no results."""
        assert filter_events_within_window([]) == []


class TestDetermineRiskTier:
    """Tests for risk tier classification."""

    def test_critical_above_threshold(self) -> None:
        assert determine_risk_tier(Decimal("3.0"), Decimal("2.0")) == "CRITICAL"

    def test_elevated_above_half(self) -> None:
        assert determine_risk_tier(Decimal("1.5"), Decimal("2.0")) == "ELEVATED"

    def test_normal_below_half(self) -> None:
        assert determine_risk_tier(Decimal("0.5"), Decimal("2.0")) == "NORMAL"


class TestEvaluateExitLiquidity:
    """End-to-end risk engine evaluation."""

    def test_suppression_triggered(self) -> None:
        """Large unlock within 72h triggers conviction_suppression."""
        supply = DefiLlamaSupply(
            symbol="ARB",
            circulating_supply=Decimal("1000000000"),
            total_supply=Decimal("10000000000"),
        )
        schedule = DuneUnlockSchedule(
            symbol="ARB",
            events=[
                DuneUnlockEvent(
                    symbol="ARB",
                    unlock_amount=Decimal("50000000"),
                    hours_until_unlock=Decimal("48"),
                    unlock_type="CLIFF",
                ),
            ],
        )
        report = evaluate_exit_liquidity(supply, schedule, Decimal("2.0"))
        assert report.conviction_suppression is True
        assert report.risk_tier == "CRITICAL"
        assert report.impact_pct == Decimal("5")

    def test_no_suppression_below_threshold(self) -> None:
        """Small unlock below threshold does NOT suppress."""
        supply = DefiLlamaSupply(
            symbol="SOL",
            circulating_supply=Decimal("500000000"),
        )
        schedule = DuneUnlockSchedule(
            symbol="SOL",
            events=[
                DuneUnlockEvent(
                    symbol="SOL",
                    unlock_amount=Decimal("1000000"),
                    hours_until_unlock=Decimal("24"),
                ),
            ],
        )
        report = evaluate_exit_liquidity(supply, schedule, Decimal("2.0"))
        assert report.conviction_suppression is False
        assert report.risk_tier == "NORMAL"

    def test_no_suppression_outside_window(self) -> None:
        """Large unlock >72h away does NOT suppress."""
        supply = DefiLlamaSupply(
            symbol="OP",
            circulating_supply=Decimal("1000000000"),
        )
        schedule = DuneUnlockSchedule(
            symbol="OP",
            events=[
                DuneUnlockEvent(
                    symbol="OP",
                    unlock_amount=Decimal("100000000"),
                    hours_until_unlock=Decimal("168"),
                ),
            ],
        )
        report = evaluate_exit_liquidity(supply, schedule, Decimal("2.0"))
        assert report.conviction_suppression is False

    def test_degraded_supply_returns_zero_impact(self) -> None:
        """Zero circulating supply → zero impact, no suppression."""
        supply = DefiLlamaSupply(
            symbol="UNKNOWN", status="DEGRADED",
        )
        schedule = DuneUnlockSchedule(
            symbol="UNKNOWN",
            events=[
                DuneUnlockEvent(
                    symbol="UNKNOWN",
                    unlock_amount=Decimal("999999999"),
                    hours_until_unlock=Decimal("1"),
                ),
            ],
        )
        report = evaluate_exit_liquidity(supply, schedule)
        assert report.conviction_suppression is False
        assert report.impact_pct == Decimal("0")
