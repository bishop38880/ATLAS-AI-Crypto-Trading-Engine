"""Tests for GNN model heads — 5 tests covering all architectures."""

from __future__ import annotations

import time

import torch
import pytest
from torch_geometric.data import Data

from atlas.agents.gnn.models import (
    DEFAULT_EMBED_DIM,
    GATHead,
    GINHead,
    GraphSAGEHead,
    MultiGraphCrossAttention,
)


def _make_graph(n_nodes: int, n_edges: int, feat_dim: int) -> Data:
    """Create a synthetic graph for testing."""
    x = torch.randn(n_nodes, feat_dim, dtype=torch.float32)
    if n_edges > 0 and n_nodes > 1:
        src = torch.randint(0, n_nodes, (n_edges,), dtype=torch.int64)
        dst = torch.randint(0, n_nodes, (n_edges,), dtype=torch.int64)
        edge_index = torch.stack([src, dst], dim=0)
    else:
        edge_index = torch.zeros(2, 0, dtype=torch.int64)
    return Data(x=x, edge_index=edge_index)


def _empty_graph(feat_dim: int) -> Data:
    """Create a zero-node, zero-edge graph."""
    return Data(
        x=torch.zeros(0, feat_dim, dtype=torch.float32),
        edge_index=torch.zeros(2, 0, dtype=torch.int64),
    )


class TestGraphSAGEHead:
    """GraphSAGE model head tests."""

    def test_forward_output_shape(self) -> None:
        """Known input → expected output shape (1, embed_dim)."""
        model = GraphSAGEHead(in_channels=8, hidden_dim=DEFAULT_EMBED_DIM)
        graph = _make_graph(n_nodes=10, n_edges=20, feat_dim=8)
        with torch.no_grad():
            out = model(graph)
        assert out.shape == (1, DEFAULT_EMBED_DIM)
        assert out.dtype == torch.float32


class TestGATHead:
    """GAT model head tests."""

    def test_forward_float32(self) -> None:
        """GAT forward pass produces float32 output."""
        model = GATHead(in_channels=6, hidden_dim=DEFAULT_EMBED_DIM, heads=2)
        graph = _make_graph(n_nodes=15, n_edges=30, feat_dim=6)
        with torch.no_grad():
            out = model(graph)
        assert out.shape == (1, DEFAULT_EMBED_DIM)
        assert out.dtype == torch.float32


class TestGINHead:
    """GIN model head tests."""

    def test_empty_graph_zero_tensor(self) -> None:
        """Empty graph → zero tensor of correct shape."""
        model = GINHead(in_channels=4, hidden_dim=DEFAULT_EMBED_DIM)
        graph = _empty_graph(feat_dim=4)
        with torch.no_grad():
            out = model(graph)
        assert out.shape == (1, DEFAULT_EMBED_DIM)
        assert torch.all(out == 0.0)


class TestMultiGraphCrossAttention:
    """Cross-attention fusion tests."""

    def test_fuse_three_embeddings(self) -> None:
        """Fuses 3 embeddings → single fixed-dim vector."""
        fuser = MultiGraphCrossAttention(
            embed_dim=DEFAULT_EMBED_DIM, num_heads=2,
        )
        emb1 = torch.randn(1, DEFAULT_EMBED_DIM)
        emb2 = torch.randn(1, DEFAULT_EMBED_DIM)
        emb3 = torch.randn(1, DEFAULT_EMBED_DIM)
        with torch.no_grad():
            out = fuser(emb1, emb2, emb3)
        assert out.shape == (1, DEFAULT_EMBED_DIM)
        assert out.dtype == torch.float32


class TestParamBudget:
    """Parameter budget sanity check."""

    def test_total_params_under_50k(self) -> None:
        """Total parameter count across all heads ≤ 50K."""
        sage = GraphSAGEHead(in_channels=8)
        gat = GATHead(in_channels=6)
        gin = GINHead(in_channels=4)
        fuser = MultiGraphCrossAttention()

        total = sum(
            sum(p.numel() for p in model.parameters())
            for model in [sage, gat, gin, fuser]
        )
        assert total <= 50_000, (
            "Total params {} exceeds 50K budget".format(total)
        )
