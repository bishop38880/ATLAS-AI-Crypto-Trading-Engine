"""Unit tests for canonical v6.1 scoring weights."""

from __future__ import annotations

import pytest

from atlas.scoring.scoring_weights import (
    STRONG_MIN,
    WEAK_MIN,
    classify_signal_strength,
    get_leverage_reference,
    get_position_size_pct,
)


def test_classify_signal_strength_tiers() -> None:
    """Boundaries match White Paper ladder."""
    assert classify_signal_strength(220) == "STRONG"
    assert classify_signal_strength(STRONG_MIN) == "STRONG"
    assert classify_signal_strength(STRONG_MIN - 1) == "MODERATE"
    assert classify_signal_strength(150) == "MODERATE"
    assert classify_signal_strength(149) == "WEAK"
    assert classify_signal_strength(WEAK_MIN) == "WEAK"
    assert classify_signal_strength(WEAK_MIN - 1) == "NO_TRADE"


def test_get_position_size_pct_matches_tiers() -> None:
    """Sizing uses 5% / 3% / 2% before overlays."""
    assert get_position_size_pct(200) == 5.0
    assert get_position_size_pct(160) == 3.0
    assert get_position_size_pct(125) == 2.0
    assert get_position_size_pct(119) == 0.0


def test_get_leverage_reference_matches_tiers() -> None:
    """Reference leverage follows strong/moderate/weak ladder."""
    assert get_leverage_reference(180) == 5
    assert get_leverage_reference(150) == 3
    assert get_leverage_reference(120) == 2
    assert get_leverage_reference(119) == 0


@pytest.mark.parametrize(
    ("total", "expected"),
    [(150, 3.0), (151, 3.0)],
)
def test_moderate_tier_uses_three_percent_not_three_point_five(
    total: int,
    expected: float,
) -> None:
    """Regression: v6.1 specifies 3% at moderate, not 3.5%."""
    assert get_position_size_pct(total) == expected
