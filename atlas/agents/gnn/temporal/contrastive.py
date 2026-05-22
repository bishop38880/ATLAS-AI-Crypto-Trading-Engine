"""Contrastive pretraining — GraphCL + InfoNCE for T-HeteroGNN.

Offline pretraining pipeline:
1. Build token correlation graphs from historical OHLCV data
2. Apply GraphCL augmentations (node drop, edge drop, feature mask, subgraph)
3. Train encoder with InfoNCE contrastive loss
4. Save pretrained weights to shadow/pretrained/hgt_contrastive.pt

CPU float32 only. No CUDA. OFFLINE — not in live scoring pipeline.
This module never touches the live confluence score.
"""

from __future__ import annotations

from pathlib import Path

import torch
import torch.nn as nn
import torch.nn.functional as F
from loguru import logger
from torch_geometric.data import Data


# ── Constants ────────────────────────────────────────────────────────

DEFAULT_HIDDEN_DIM = 32
DEFAULT_PROJ_DIMS = (32, 16, 8)
DEFAULT_TEMPERATURE = 0.5
DEFAULT_MAX_EPOCHS = 5
DEFAULT_PATIENCE = 2
NODE_DROP_RATIO = 0.15
EDGE_DROP_RATIO = 0.20
FEATURE_MASK_RATIO = 0.10
RWR_WALK_LENGTH = 20
PRETRAINED_DIR = Path("shadow/pretrained")
PRETRAINED_FILENAME = "hgt_contrastive.pt"


# ── Augmentations ────────────────────────────────────────────────────


class GraphCLAugmenter:
    """GraphCL-style graph augmentations for contrastive learning.

    Produces 4 augmented views of an input graph:
    1. Node drop (15%)
    2. Edge drop (20%)
    3. Feature masking (10%)
    4. Subgraph sampling (RWR, length 20)
    """

    def node_drop(
        self,
        data: Data,
        ratio: float = NODE_DROP_RATIO,
    ) -> Data:
        """Drop random nodes and reindex edges.

        Args:
            data: Input graph.
            ratio: Fraction of nodes to drop.

        Returns:
            Augmented graph with surviving nodes.
        """
        assert data.x is not None
        assert data.edge_index is not None
        n = data.x.shape[0]
        if n <= 1:
            return data
        n_keep = max(1, int(n * (1.0 - ratio)))
        perm = torch.randperm(n)[:n_keep]
        perm_sorted, _ = perm.sort()
        return self._reindex_graph(data, perm_sorted)

    def _reindex_graph(
        self,
        data: Data,
        keep_indices: torch.Tensor,
    ) -> Data:
        """Reindex graph to only include kept nodes."""
        assert data.x is not None
        assert data.edge_index is not None
        n_old = data.x.shape[0]
        x_new = data.x[keep_indices]
        mapping = torch.full((n_old,), -1, dtype=torch.long)
        mapping[keep_indices] = torch.arange(
            keep_indices.shape[0], dtype=torch.long,
        )
        src = mapping[data.edge_index[0]]
        dst = mapping[data.edge_index[1]]
        valid = (src >= 0) & (dst >= 0)
        edge_index = torch.stack([src[valid], dst[valid]], dim=0)
        return Data(x=x_new, edge_index=edge_index)

    def edge_drop(
        self,
        data: Data,
        ratio: float = EDGE_DROP_RATIO,
    ) -> Data:
        """Drop random edges from the graph.

        Args:
            data: Input graph.
            ratio: Fraction of edges to drop.

        Returns:
            Augmented graph with surviving edges.
        """
        assert data.x is not None
        assert data.edge_index is not None
        n_edges = data.edge_index.shape[1]
        if n_edges == 0:
            return data
        n_keep = max(0, int(n_edges * (1.0 - ratio)))
        perm = torch.randperm(n_edges)[:n_keep]
        edge_index = data.edge_index[:, perm]
        return Data(x=data.x.clone(), edge_index=edge_index)

    def feature_mask(
        self,
        data: Data,
        ratio: float = FEATURE_MASK_RATIO,
    ) -> Data:
        """Mask random feature dimensions with zero.

        Args:
            data: Input graph.
            ratio: Fraction of feature dimensions to mask.

        Returns:
            Augmented graph with masked features.
        """
        assert data.x is not None
        assert data.edge_index is not None
        feat_dim = data.x.shape[1]
        n_mask = max(1, int(feat_dim * ratio))
        mask_dims = torch.randperm(feat_dim)[:n_mask]
        x_new = data.x.clone()
        x_new[:, mask_dims] = 0.0
        return Data(x=x_new, edge_index=data.edge_index.clone())

    def subgraph_sample(
        self,
        data: Data,
        walk_length: int = RWR_WALK_LENGTH,
    ) -> Data:
        """Random walk with restart subgraph sampling.

        Args:
            data: Input graph.
            walk_length: Number of walk steps.

        Returns:
            Subgraph induced by visited nodes.
        """
        assert data.x is not None
        assert data.edge_index is not None
        n = data.x.shape[0]
        if n <= 1:
            return data
        visited = self._random_walk_with_restart(
            data.edge_index, n, walk_length,
        )
        visited_sorted, _ = visited.sort()
        return self._reindex_graph(data, visited_sorted)

    def _random_walk_with_restart(
        self,
        edge_index: torch.Tensor,
        n: int,
        walk_length: int,
        restart_prob: float = 0.15,
    ) -> torch.Tensor:
        """Execute RWR and return unique visited node set."""
        start = int(torch.randint(0, n, (1,)).item())
        current = start
        visited: set[int] = {current}

        adj = self._build_adjacency(edge_index, n)
        for _ in range(walk_length):
            if torch.rand(1).item() < restart_prob:
                current = start
            else:
                neighbors = adj.get(current, [])
                if neighbors:
                    idx = int(torch.randint(0, len(neighbors), (1,)).item())
                    current = neighbors[idx]
            visited.add(current)

        return torch.tensor(sorted(visited), dtype=torch.long)

    def _build_adjacency(
        self,
        edge_index: torch.Tensor,
        n: int,
    ) -> dict[int, list[int]]:
        """Build adjacency list from edge_index."""
        adj: dict[int, list[int]] = {}
        for i in range(edge_index.shape[1]):
            src = int(edge_index[0, i])
            dst = int(edge_index[1, i])
            if src not in adj:
                adj[src] = []
            adj[src].append(dst)
        return adj

    def augment_all(self, data: Data) -> list[Data]:
        """Generate all 4 augmented views.

        Args:
            data: Input graph.

        Returns:
            List of 4 augmented views.
        """
        return [
            self.node_drop(data),
            self.edge_drop(data),
            self.feature_mask(data),
            self.subgraph_sample(data),
        ]


# ── Projection Head ─────────────────────────────────────────────────


class ContrastiveProjectionHead(nn.Module):
    """MLP projection head: 32 → 16 → 8.

    Projects graph-level embeddings into contrastive space.

    Args:
        dims: Tuple of (input, hidden, output) dimensions.
    """

    def __init__(
        self,
        dims: tuple[int, int, int] = DEFAULT_PROJ_DIMS,
    ) -> None:
        super().__init__()
        self.mlp = nn.Sequential(
            nn.Linear(dims[0], dims[1]),
            nn.ReLU(),
            nn.Linear(dims[1], dims[2]),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """Project embeddings to contrastive space."""
        return self.mlp(x)


# ── InfoNCE Loss ─────────────────────────────────────────────────────


def info_nce_loss(
    z1: torch.Tensor,
    z2: torch.Tensor,
    temperature: float = DEFAULT_TEMPERATURE,
) -> torch.Tensor:
    """InfoNCE contrastive loss with in-batch negatives.

    Args:
        z1: (B, D) anchor projections.
        z2: (B, D) positive projections.
        temperature: Softmax temperature τ.

    Returns:
        Scalar InfoNCE loss.
    """
    z1 = F.normalize(z1, dim=-1)
    z2 = F.normalize(z2, dim=-1)
    sim = torch.mm(z1, z2.t()) / temperature
    labels = torch.arange(z1.shape[0], dtype=torch.long)
    loss_12 = F.cross_entropy(sim, labels)
    loss_21 = F.cross_entropy(sim.t(), labels)
    return (loss_12 + loss_21) / 2.0


# ── Lightweight Encoder for Contrastive ──────────────────────────────


class _ContrastiveEncoder(nn.Module):
    """Lightweight GNN encoder for contrastive pretraining.

    Uses the same architecture pattern as THeteroGNN but operates
    on homogeneous Data objects for the correlation graph corpus.
    Shares weight keys so pretrained weights transfer to THeteroGNN.

    Args:
        in_channels: Input feature dimension.
        hidden_dim: Hidden dimension.
    """

    def __init__(
        self,
        in_channels: int = 8,
        hidden_dim: int = DEFAULT_HIDDEN_DIM,
    ) -> None:
        super().__init__()
        self.input_proj = nn.Linear(in_channels, hidden_dim)
        self.layer1 = nn.Linear(hidden_dim, hidden_dim)
        self.layer2 = nn.Linear(hidden_dim, hidden_dim)
        self.norm1 = nn.LayerNorm(hidden_dim)
        self.norm2 = nn.LayerNorm(hidden_dim)
        self.hidden_dim = hidden_dim

    def forward(self, data: Data) -> torch.Tensor:
        """Encode graph to a single embedding vector.

        Args:
            data: Input graph with x and edge_index.

        Returns:
            (1, hidden_dim) graph-level embedding.
        """
        assert data.x is not None
        if data.x.shape[0] == 0:
            return torch.zeros(1, self.hidden_dim, dtype=torch.float32)
        x = F.relu(self.input_proj(data.x))
        x = self.norm1(F.relu(self.layer1(x)))
        x = self.norm2(F.relu(self.layer2(x)))
        return x.mean(dim=0, keepdim=True)


# ── Contrastive Pretrainer ───────────────────────────────────────────


class ContrastivePretrainer:
    """Offline contrastive pretrainer for GNN encoder.

    Runs GraphCL-style contrastive learning with InfoNCE loss.
    CPU-only, max 5 epochs, saves pretrained encoder weights.

    This is NOT part of the live scoring pipeline. It runs offline
    and produces a weight file for THeteroGNN to load on init.

    Args:
        in_channels: Input feature dimension.
        hidden_dim: Encoder hidden dimension.
        max_epochs: Maximum training epochs.
        patience: Early stopping patience.
        temperature: InfoNCE temperature.
    """

    def __init__(
        self,
        in_channels: int = 8,
        hidden_dim: int = DEFAULT_HIDDEN_DIM,
        max_epochs: int = DEFAULT_MAX_EPOCHS,
        patience: int = DEFAULT_PATIENCE,
        temperature: float = DEFAULT_TEMPERATURE,
    ) -> None:
        self.encoder = _ContrastiveEncoder(in_channels, hidden_dim)
        self.projection = ContrastiveProjectionHead()
        self.augmenter = GraphCLAugmenter()
        self.max_epochs = max_epochs
        self.patience = patience
        self.temperature = temperature

    def pretrain(
        self,
        graphs: list[Data],
        batch_size: int = 32,
    ) -> float:
        """Run contrastive pretraining on graph corpus.

        Args:
            graphs: List of training graphs.
            batch_size: Mini-batch size.

        Returns:
            Final training loss.
        """
        if not graphs:
            logger.warning("contrastive_pretrain_empty_corpus")
            return float("inf")

        optimizer = self._build_optimizer()
        best_loss = float("inf")
        patience_counter = 0
        final_loss = float("inf")

        for epoch in range(self.max_epochs):
            loss = self._train_epoch(graphs, optimizer, batch_size)
            final_loss = loss
            logger.info(
                "contrastive_epoch | epoch={} | loss={:.4f}",
                epoch, loss,
            )
            best_loss, patience_counter, stop = self._check_early_stop(
                loss, best_loss, patience_counter, epoch,
            )
            if stop:
                break

        self._save_encoder()
        return final_loss

    def _build_optimizer(self) -> torch.optim.Optimizer:
        """Create Adam optimizer over encoder + projection params."""
        return torch.optim.Adam(
            list(self.encoder.parameters())
            + list(self.projection.parameters()),
            lr=1e-3,
        )

    def _check_early_stop(
        self,
        loss: float,
        best_loss: float,
        patience_counter: int,
        epoch: int,
    ) -> tuple[float, int, bool]:
        """Check early-stopping condition and return updated state."""
        if loss < best_loss - 1e-4:
            return loss, 0, False
        patience_counter += 1
        if patience_counter >= self.patience:
            logger.info("contrastive_early_stop | epoch={}", epoch)
            return best_loss, patience_counter, True
        return best_loss, patience_counter, False

    def _train_epoch(
        self,
        graphs: list[Data],
        optimizer: torch.optim.Optimizer,
        batch_size: int,
    ) -> float:
        """Execute one training epoch over graph corpus."""
        self.encoder.train()
        self.projection.train()
        total_loss = 0.0
        n_batches = 0
        perm = torch.randperm(len(graphs))

        for start in range(0, len(graphs), batch_size):
            batch_idx = perm[start:start + batch_size]
            loss = self._train_batch(graphs, batch_idx, optimizer)
            total_loss += loss
            n_batches += 1

        return total_loss / max(n_batches, 1)

    def _train_batch(
        self,
        graphs: list[Data],
        batch_idx: torch.Tensor,
        optimizer: torch.optim.Optimizer,
    ) -> float:
        """Train on a single mini-batch of graphs."""
        z1_list: list[torch.Tensor] = []
        z2_list: list[torch.Tensor] = []

        for idx in batch_idx:
            g = graphs[int(idx)]
            augs = self.augmenter.augment_all(g)
            pair = augs[:2]
            z1_list.append(self.projection(self.encoder(pair[0])))
            z2_list.append(self.projection(self.encoder(pair[1])))

        z1 = torch.cat(z1_list, dim=0)
        z2 = torch.cat(z2_list, dim=0)
        loss = info_nce_loss(z1, z2, self.temperature)
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()
        return float(loss.item())

    def _save_encoder(self) -> None:
        """Save encoder weights to pretrained directory."""
        out_dir = PRETRAINED_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / PRETRAINED_FILENAME
        torch.save(self.encoder.state_dict(), path)
        logger.info("contrastive_encoder_saved | path={}", str(path))

    def get_encoder_state(self) -> dict[str, torch.Tensor]:
        """Return encoder state dict for inspection."""
        return {
            k: v.clone()
            for k, v in self.encoder.state_dict().items()
        }
