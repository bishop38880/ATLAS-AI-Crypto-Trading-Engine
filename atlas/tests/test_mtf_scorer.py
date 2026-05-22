"""
Unit tests for the MTF confluence scoring engine.
All score assertions use exact integer comparison — Decimal arithmetic
must produce deterministic results.
"""

from decimal import Decimal
from datetime import datetime, timedelta, timezone

import pytest

from atlas.scoring.mtf_scorer import (
    STALE_15M,
    STALE_30M,
    STALE_4H,
    _detect_alignment,
    _multiplier_for,
    compute_mtf_block,
    mtf_to_decision_label,
)


def fresh_ts(offset_minutes: int = 0) -> str:
    """Returns an ISO timestamp N minutes in the past."""
    t = datetime.now(timezone.utc) - timedelta(minutes=offset_minutes)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


class TestAlignmentDetection:
    """Alignment classification across horizons."""

    def test_building(self) -> None:
        assert _detect_alignment(100, 140, 180) == "BUILDING"

    def test_fading(self) -> None:
        assert _detect_alignment(180, 140, 100) == "FADING"

    def test_flat_within_threshold(self) -> None:
        assert _detect_alignment(155, 160, 162) == "FLAT"

    def test_flat_exactly_at_threshold(self) -> None:
        assert _detect_alignment(150, 158, 165) == "FLAT"

    def test_recovering(self) -> None:
        assert _detect_alignment(160, 120, 175) == "RECOVERING"

    def test_building_requires_strict_ordering(self) -> None:
        assert _detect_alignment(140, 140, 180) != "BUILDING"


class TestMultipliers:
    """Per-alignment multiplier constants."""

    def test_building_multiplier(self) -> None:
        assert _multiplier_for("BUILDING") == Decimal("1.10")

    def test_fading_multiplier(self) -> None:
        assert _multiplier_for("FADING") == Decimal("0.88")

    def test_flat_multiplier(self) -> None:
        assert _multiplier_for("FLAT") == Decimal("1.00")

    def test_recovering_multiplier(self) -> None:
        assert _multiplier_for("RECOVERING") == Decimal("1.00")


class TestWeightedAverage:
    """Fusion math and BUILDING/FADING boosts."""

    def test_equal_scores_flat(self) -> None:
        block = compute_mtf_block(
            160,
            160,
            160,
            fresh_ts(),
            fresh_ts(),
            fresh_ts(),
        )
        assert block.base_avg == 160
        assert block.avg == 160
        assert block.alignment == "FLAT"

    def test_building_boosts_avg(self) -> None:
        block = compute_mtf_block(
            120,
            150,
            180,
            fresh_ts(),
            fresh_ts(),
            fresh_ts(),
        )
        assert block.base_avg == 141
        assert block.avg == 155
        assert block.alignment == "BUILDING"

    def test_fading_penalises_avg(self) -> None:
        block = compute_mtf_block(
            180,
            150,
            120,
            fresh_ts(),
            fresh_ts(),
            fresh_ts(),
        )
        assert block.base_avg == 159
        assert block.avg == 140
        assert block.alignment == "FADING"

    def test_avg_capped_at_220(self) -> None:
        block = compute_mtf_block(
            220,
            220,
            220,
            fresh_ts(),
            fresh_ts(),
            fresh_ts(),
        )
        assert block.avg == 220


class TestStaleness:
    """Stale packets short-circuit to zero avg."""

    def test_stale_4h_returns_no_trade_block(self) -> None:
        block = compute_mtf_block(
            180,
            160,
            170,
            fresh_ts(offset_minutes=STALE_4H + 1),
            fresh_ts(),
            fresh_ts(),
        )
        assert block.is_stale is True
        assert block.stale_tf == "4h"
        assert block.avg == 0

    def test_stale_30m_returns_no_trade_block(self) -> None:
        block = compute_mtf_block(
            180,
            160,
            170,
            fresh_ts(),
            fresh_ts(offset_minutes=STALE_30M + 1),
            fresh_ts(),
        )
        assert block.is_stale is True
        assert block.stale_tf == "30m"

    def test_stale_15m_returns_no_trade_block(self) -> None:
        block = compute_mtf_block(
            180,
            160,
            170,
            fresh_ts(),
            fresh_ts(),
            fresh_ts(offset_minutes=STALE_15M + 1),
        )
        assert block.is_stale is True
        assert block.stale_tf == "15m"

    def test_fresh_all_tfs_not_stale(self) -> None:
        block = compute_mtf_block(
            180,
            160,
            170,
            fresh_ts(5),
            fresh_ts(5),
            fresh_ts(5),
        )
        assert block.is_stale is False


class TestDecisionLabels:
    """FADING downgrades versus raw avg tiers."""

    def test_strong_building(self) -> None:
        assert mtf_to_decision_label(175, "BUILDING") == "STRONG"

    def test_strong_fading_downgraded_to_buy(self) -> None:
        assert mtf_to_decision_label(175, "FADING") == "BUY"

    def test_buy_fading_downgraded_to_weak(self) -> None:
        assert mtf_to_decision_label(155, "FADING") == "WEAK"

    def test_weak_signal(self) -> None:
        assert mtf_to_decision_label(135, "FLAT") == "WEAK"

    def test_no_trade_below_threshold(self) -> None:
        assert mtf_to_decision_label(129, "BUILDING") == "NO_TRADE"

    def test_no_trade_even_building_if_avg_too_low(self) -> None:
        assert mtf_to_decision_label(108, "BUILDING") == "NO_TRADE"
