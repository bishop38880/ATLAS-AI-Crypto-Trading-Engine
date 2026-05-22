"""Tests for graph_builder — 5 tests covering all three graph types."""

from __future__ import annotations

import torch
import pytest
from torch_geometric.data import Data

from atlas.agents.gnn.graph_builder import (
    TOKEN_FEATURE_DIM,
    WALLET_FEATURE_DIM,
    DEFI_FEATURE_DIM,
    build_defi_graph,
    build_token_correlation_graph,
    build_transaction_graph,
)


def _make_asset_features(n: int) -> list[dict[str, float]]:
    """Generate n synthetic asset feature dicts."""
    features = []
    for i in range(n):
        features.append({
            "log_mcap": 10.0 + i * 0.1,
            "log_volume_24h": 8.0 + i * 0.05,
            "returns_1h": 0.01 * (i - n // 2),
            "returns_24h": 0.02 * (i - n // 2),
            "volatility_1h": 0.03 + i * 0.001,
            "volatility_24h": 0.05 + i * 0.002,
            "rsi_14": 50.0 + i,
            "atr_normalized": 0.02 + i * 0.001,
        })
    return features


def _make_correlated_returns(n: int, t: int) -> list[list[float]]:
    """Generate returns where first two assets are highly correlated."""
    import random
    random.seed(42)
    base = [random.gauss(0, 0.01) for _ in range(t)]
    rows: list[list[float]] = []
    for i in range(n):
        if i < 2:
            noise = [random.gauss(0, 0.001) for _ in range(t)]
            rows.append([b + n_ for b, n_ in zip(base, noise)])
        else:
            rows.append([random.gauss(0, 0.01) for _ in range(t)])
    return rows


class TestTokenCorrelationGraph:
    """Token correlation graph builder tests."""

    def test_basic_graph_shape(self) -> None:
        """33 assets in → edge_index shape[0] == 2, edges symmetric."""
        n = 33
        features = _make_asset_features(n)
        returns = _make_correlated_returns(n, 60)
        data = build_token_correlation_graph(features, returns)

        assert isinstance(data, Data)
        assert data.x is not None
        assert data.edge_index is not None
        assert data.x.shape == (n, TOKEN_FEATURE_DIM)
        assert data.edge_index.shape[0] == 2
        # Edges are symmetric: every (i,j) has a (j,i)
        edges: set[tuple[int, int]] = set()
        ei = data.edge_index
        for k in range(ei.shape[1]):
            edges.add((int(ei[0, k]), int(ei[1, k])))
        for s, d in list(edges):
            assert (d, s) in edges, "Edges must be symmetric"

    def test_empty_ohlcv_no_crash(self) -> None:
        """Empty asset list → valid empty graph, no exception."""
        data = build_token_correlation_graph([], [])
        assert isinstance(data, Data)
        assert data.x is not None
        assert data.edge_index is not None
        assert data.x.shape[0] == 0
        assert data.x.shape[1] == TOKEN_FEATURE_DIM
        assert data.edge_index.shape == (2, 0)


class TestTransactionGraph:
    """Transaction graph builder tests."""

    def test_correct_node_count_and_directed(self) -> None:
        """Mock Nansen data → correct node count, directed edges."""
        n_wallets = 10
        wallet_feats = [
            {
                "log_balance_usd": 5.0 + i,
                "tx_count_24h": float(i * 3),
                "unique_counterparties": float(i),
                "wallet_age_days": 100.0 + i,
                "smart_money_score": 0.5,
                "cex_flow_net_24h": 0.0,
            }
            for i in range(n_wallets)
        ]
        transfers = [(0, 1), (1, 2), (3, 4)]
        data = build_transaction_graph(wallet_feats, transfers)

        assert data.x is not None
        assert data.edge_index is not None
        assert data.x.shape == (n_wallets, WALLET_FEATURE_DIM)
        assert data.edge_index.shape == (2, 3)
        # Directed: (0→1) exists but (1→0) may not
        edges: set[tuple[int, int]] = set()
        for k in range(data.edge_index.shape[1]):
            edges.add((
                int(data.edge_index[0, k]),
                int(data.edge_index[1, k]),
            ))
        assert (0, 1) in edges
        assert (1, 0) not in edges


class TestDeFiStubGraph:
    """DeFi stub graph tests."""

    def test_stub_returns_valid_graph(self) -> None:
        """Stub returns 1-node zero-edge graph without crashing."""
        data = build_defi_graph()
        assert isinstance(data, Data)
        assert data.x is not None
        assert data.edge_index is not None
        assert data.x.shape == (1, DEFI_FEATURE_DIM)
        assert data.edge_index.shape == (2, 0)
        assert torch.all(data.x == 0.0)


class TestFeatureDtype:
    """Feature dtype validation tests."""

    def test_all_node_features_float32(self) -> None:
        """All graph builders produce float32 node features."""
        corr = build_token_correlation_graph(
            _make_asset_features(5),
            _make_correlated_returns(5, 30),
        )
        assert corr.x is not None
        assert corr.x.dtype == torch.float32

        tx = build_transaction_graph(
            [{"log_balance_usd": 1.0} for _ in range(3)],
            [(0, 1)],
        )
        assert tx.x is not None
        assert tx.x.dtype == torch.float32

        defi = build_defi_graph()
        assert defi.x is not None
        assert defi.x.dtype == torch.float32
