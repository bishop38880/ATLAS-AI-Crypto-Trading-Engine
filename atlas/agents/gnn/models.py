"""GNN model heads — GraphSAGE, GAT, GIN, and MultiGraphCrossAttention.

All operations are CPU float32. No CUDA calls. Empty-graph safe.
Forward pass is pure torch — ``asyncio.to_thread`` wrapping happens
at async call sites, not inside the model.

Parameter budget: ~45K total (CPU float32, forward pass < 20ms).
"""

from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch_geometric.data import Data
from torch_geometric.nn import SAGEConv, GATConv, GINConv, global_mean_pool


DEFAULT_EMBED_DIM = 32


# ── GraphSAGE Head (~12K params) ────────────────────────────────────


class GraphSAGEHead(nn.Module):
    """2-layer GraphSAGE with mean aggregation.

    Args:
        in_channels: Input feature dimension.
        hidden_dim: Hidden and output dimension (default 32).
    """

    def __init__(
        self,
        in_channels: int,
        hidden_dim: int = DEFAULT_EMBED_DIM,
    ) -> None:
        super().__init__()
        self.conv1 = SAGEConv(in_channels, hidden_dim, aggr="mean")
        self.conv2 = SAGEConv(hidden_dim, hidden_dim, aggr="mean")
        self.hidden_dim = hidden_dim

    def forward(self, data: Data) -> torch.Tensor:
        """Forward pass — returns graph-level embedding (1, hidden_dim)."""
        assert data.x is not None
        assert data.edge_index is not None
        if data.x.shape[0] == 0:
            return torch.zeros(1, self.hidden_dim, dtype=torch.float32)
        x = F.relu(self.conv1(data.x, data.edge_index))
        x = self.conv2(x, data.edge_index)
        batch = _get_batch(data)
        return global_mean_pool(x, batch)


# ── GAT Head (~15K params) ──────────────────────────────────────────


class GATHead(nn.Module):
    """2-layer GAT with 2 attention heads.

    Args:
        in_channels: Input feature dimension.
        hidden_dim: Per-head hidden dimension (default 32).
        heads: Number of attention heads (default 2).
    """

    def __init__(
        self,
        in_channels: int,
        hidden_dim: int = DEFAULT_EMBED_DIM,
        heads: int = 2,
    ) -> None:
        super().__init__()
        self.conv1 = GATConv(in_channels, hidden_dim, heads=heads, concat=True)
        self.conv2 = GATConv(
            hidden_dim * heads, hidden_dim, heads=1, concat=False,
        )
        self.hidden_dim = hidden_dim

    def forward(self, data: Data) -> torch.Tensor:
        """Forward pass — returns graph-level embedding (1, hidden_dim)."""
        assert data.x is not None
        assert data.edge_index is not None
        if data.x.shape[0] == 0:
            return torch.zeros(1, self.hidden_dim, dtype=torch.float32)
        x = F.elu(self.conv1(data.x, data.edge_index))
        x = self.conv2(x, data.edge_index)
        batch = _get_batch(data)
        return global_mean_pool(x, batch)


# ── GIN Head (~10K params) ──────────────────────────────────────────


class GINHead(nn.Module):
    """2-layer GIN (Graph Isomorphism Network).

    Args:
        in_channels: Input feature dimension.
        hidden_dim: Hidden and output dimension (default 32).
    """

    def __init__(
        self,
        in_channels: int,
        hidden_dim: int = DEFAULT_EMBED_DIM,
    ) -> None:
        super().__init__()
        self.conv1 = GINConv(
            nn.Sequential(
                nn.Linear(in_channels, hidden_dim),
                nn.ReLU(),
                nn.Linear(hidden_dim, hidden_dim),
            ),
        )
        self.conv2 = GINConv(
            nn.Sequential(
                nn.Linear(hidden_dim, hidden_dim),
                nn.ReLU(),
                nn.Linear(hidden_dim, hidden_dim),
            ),
        )
        self.hidden_dim = hidden_dim

    def forward(self, data: Data) -> torch.Tensor:
        """Forward pass — returns graph-level embedding (1, hidden_dim)."""
        assert data.x is not None
        assert data.edge_index is not None
        if data.x.shape[0] == 0:
            return torch.zeros(1, self.hidden_dim, dtype=torch.float32)
        x = F.relu(self.conv1(data.x, data.edge_index))
        x = self.conv2(x, data.edge_index)
        batch = _get_batch(data)
        return global_mean_pool(x, batch)


# ── MultiGraphCrossAttention (~8K params) ───────────────────────────


class MultiGraphCrossAttention(nn.Module):
    """Fuse three graph embeddings via multi-head cross-attention.

    Takes three (1, embed_dim) tensors and produces a single
    (1, embed_dim) fused representation.

    Args:
        embed_dim: Embedding dimension (default 32).
        num_heads: Number of attention heads (default 2).
    """

    def __init__(
        self,
        embed_dim: int = DEFAULT_EMBED_DIM,
        num_heads: int = 2,
    ) -> None:
        super().__init__()
        self.attn = nn.MultiheadAttention(
            embed_dim=embed_dim,
            num_heads=num_heads,
            batch_first=True,
        )
        self.norm = nn.LayerNorm(embed_dim)
        self.proj = nn.Linear(embed_dim, embed_dim)
        self.embed_dim = embed_dim

    def forward(
        self,
        emb_corr: torch.Tensor,
        emb_tx: torch.Tensor,
        emb_defi: torch.Tensor,
    ) -> torch.Tensor:
        """Fuse three graph embeddings into a single vector.

        Args:
            emb_corr: Token correlation embedding (1, embed_dim).
            emb_tx: Transaction embedding (1, embed_dim).
            emb_defi: DeFi embedding (1, embed_dim).

        Returns:
            Fused embedding (1, embed_dim).
        """
        # Stack as sequence: (1, 3, embed_dim)
        seq = torch.stack([emb_corr, emb_tx, emb_defi], dim=1)
        attended, _ = self.attn(seq, seq, seq)
        normed = self.norm(attended.mean(dim=1, keepdim=False))
        return self.proj(normed).unsqueeze(0) if normed.dim() == 1 else self.proj(normed)


# ── Helpers ──────────────────────────────────────────────────────────


def _get_batch(data: Data) -> torch.Tensor:
    """Return batch tensor — zeros for single-graph inference."""
    if hasattr(data, "batch") and data.batch is not None:
        return data.batch
    assert data.x is not None
    return torch.zeros(data.x.shape[0], dtype=torch.long)
