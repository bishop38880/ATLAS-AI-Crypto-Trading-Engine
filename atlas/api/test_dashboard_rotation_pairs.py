"""Dashboard ladder builder parity tests."""

from __future__ import annotations

from atlas.api.dashboard_rotation_pairs import calculate_dashboard_pairs


def test_dashboard_pairs_falls_back_to_thirty_three() -> None:
    pairs = calculate_dashboard_pairs([])
    assert len(pairs) == 33
    assert pairs[0].upper().startswith("BTC/")


def test_dashboard_pairs_respects_rotation_order() -> None:
    pairs = calculate_dashboard_pairs(["SOL/USDT", "BTC/USDT"])
    assert pairs[0] == "SOL/USDT"
    assert pairs[1] == "BTC/USDT"
