"""Tests for rolling asset correlation helpers."""

from __future__ import annotations

import pytest

from atlas.api.asset_correlation_matrix import (
    analyse_position_concentration,
    build_correlation_matrix,
    collect_extreme_pairs,
    find_high_correlation_clusters,
)


def test_pearson_matrix_identity_diagonal() -> None:
    """Diagonal stays at 1.0 when sufficient returns exist."""
    returns = [[0.01 * float(i) for i in range(40)] for _ in range(3)]
    matrix = build_correlation_matrix(returns, 14)
    for idx in range(3):
        assert matrix[idx][idx] == 1.0


def test_high_correlation_clusters_merge_expected_assets() -> None:
    """Three-way cluster emerges when mutual correlations breach threshold."""
    bases = ["A", "B", "C", "D"]
    matrix = [
        [1.0, 0.9, 0.88, 0.1],
        [0.9, 1.0, 0.87, 0.0],
        [0.88, 0.87, 1.0, -0.05],
        [0.1, 0.0, -0.05, 1.0],
    ]
    clusters = find_high_correlation_clusters(matrix, bases, edge_threshold=0.85)
    largest = max(clusters, key=lambda c: int(c["size"]))
    assert set(largest["bases"]) == {"A", "B", "C"}


def test_collect_extreme_pairs_orders_descending() -> None:
    bases = ["A", "B", "C"]
    matrix = [
        [1.0, 0.95, 0.5],
        [0.95, 1.0, 0.4],
        [0.5, 0.4, 1.0],
    ]
    pairs = collect_extreme_pairs(matrix, bases, threshold=0.92)
    assert len(pairs) == 1
    assert pairs[0]["base_a"] == "A"
    assert pairs[0]["base_b"] == "B"


@pytest.mark.parametrize(
    ("positions", "expected_level"),
    [
        (["BTC"], "ok"),
        (["BTC", "ETH"], "critical"),
    ],
)
def test_position_concentration_levels(positions: list[str], expected_level: str) -> None:
    bases = ["BTC", "ETH", "SOL"]
    matrix = [
        [1.0, 0.93, 0.4],
        [0.93, 1.0, 0.35],
        [0.4, 0.35, 1.0],
    ]
    result = analyse_position_concentration(matrix, bases, positions)
    assert result["level"] == expected_level
