"""TGN Memory — Temporal Graph Network memory module.

Implements the three-component memory update from Rossi et al., 2020:
1. Message Function: MLP([memory_i, memory_j, Δt, edge_features])
2. Memory Updater: GRUCell(aggregated_messages, previous_memory)
3. Embedding Function: attention over neighborhood memory

Memory persists across scoring cycles via ``torch.save`` / ``torch.load``.
Banned serializers are NOT used — torch.save / torch.load only.

CPU float32 only. No CUDA. Shadow mode — NEVER touches live scoring.
"""

from __future__ import annotations

from pathlib import Path

import torch
import torch.nn as nn
import torch.nn.functional as F
from loguru import logger


# ── Constants ────────────────────────────────────────────────────────

DEFAULT_MEMORY_DIM = 32
DEFAULT_MESSAGE_DIM = 16
DEFAULT_EDGE_FEAT_DIM = 1
DEFAULT_MAX_NODES = 256
MEMORY_DIR = Path("shadow/tgn_memory")


# ── TGN Memory Module ───────────────────────────────────────────────


class TGNMemory(nn.Module):
    """Temporal Graph Network memory with GRU updater.

    Maintains a per-node memory buffer that persists across cycles.
    Memory is updated via message computation + GRU aggregation.

    Args:
        max_nodes: Maximum number of tracked nodes.
        memory_dim: Dimension of per-node memory vectors.
        message_dim: Dimension of computed messages.
        edge_feat_dim: Dimension of edge features (incl. Δt).
    """

    def __init__(
        self,
        max_nodes: int = DEFAULT_MAX_NODES,
        memory_dim: int = DEFAULT_MEMORY_DIM,
        message_dim: int = DEFAULT_MESSAGE_DIM,
        edge_feat_dim: int = DEFAULT_EDGE_FEAT_DIM,
    ) -> None:
        super().__init__()
        self.max_nodes = max_nodes
        self.memory_dim = memory_dim
        self.message_dim = message_dim
        msg_input = memory_dim * 2 + 1 + edge_feat_dim
        self.msg_mlp = nn.Sequential(
            nn.Linear(msg_input, message_dim * 2),
            nn.ReLU(),
            nn.Linear(message_dim * 2, message_dim),
        )
        self.gru = nn.GRUCell(message_dim, memory_dim)
        self.attn_query = nn.Linear(memory_dim, memory_dim, bias=False)
        self.attn_key = nn.Linear(memory_dim, memory_dim, bias=False)
        self._memory = nn.Parameter(
            torch.zeros(max_nodes, memory_dim),
            requires_grad=False,
        )

    def compute_messages(
        self,
        src_ids: torch.Tensor,
        dst_ids: torch.Tensor,
        delta_t: torch.Tensor,
        edge_features: torch.Tensor | None = None,
    ) -> torch.Tensor:
        """Compute messages for interacting node pairs.

        Args:
            src_ids: (E,) source node indices.
            dst_ids: (E,) destination node indices.
            delta_t: (E,) time deltas since last interaction.
            edge_features: (E, edge_feat_dim) optional edge features.

        Returns:
            (E, message_dim) computed messages.
        """
        mem_src = self._memory[src_ids]
        mem_dst = self._memory[dst_ids]
        dt = delta_t.unsqueeze(-1).float()
        if edge_features is None:
            edge_features = torch.zeros(
                src_ids.shape[0], DEFAULT_EDGE_FEAT_DIM,
                dtype=torch.float32,
            )
        msg_input = torch.cat([mem_src, mem_dst, dt, edge_features], dim=-1)
        return self.msg_mlp(msg_input)

    def update_memory(
        self,
        node_ids: torch.Tensor,
        messages: torch.Tensor,
        src_ids: torch.Tensor,
        dst_ids: torch.Tensor,
    ) -> None:
        """Aggregate messages per node and update memory via GRU.

        Args:
            node_ids: (N,) unique node indices to update.
            messages: (E, message_dim) messages from compute_messages.
            src_ids: (E,) source node indices of messages.
            dst_ids: (E,) destination node indices of messages.
        """
        agg = self._aggregate_messages(node_ids, messages, dst_ids)
        updated = self.gru(agg, self._memory[node_ids])
        self._memory.data[node_ids] = updated.detach()

    def _aggregate_messages(
        self,
        node_ids: torch.Tensor,
        messages: torch.Tensor,
        dst_ids: torch.Tensor,
    ) -> torch.Tensor:
        """Mean-aggregate messages per destination node."""
        n = node_ids.shape[0]
        agg = torch.zeros(n, self.message_dim, dtype=torch.float32)
        counts = torch.zeros(n, 1, dtype=torch.float32)

        id_to_local = torch.zeros(
            self.max_nodes, dtype=torch.long,
        )
        id_to_local[node_ids] = torch.arange(n, dtype=torch.long)

        local_dst = id_to_local[dst_ids]
        agg.scatter_add_(0, local_dst.unsqueeze(1).expand_as(messages), messages)
        ones = torch.ones(dst_ids.shape[0], 1, dtype=torch.float32)
        counts.scatter_add_(0, local_dst.unsqueeze(1), ones)
        return agg / counts.clamp(min=1.0)

    def compute_embedding(
        self,
        node_ids: torch.Tensor,
        neighbor_ids: torch.Tensor | None = None,
    ) -> torch.Tensor:
        """Compute temporal embedding via attention over neighbors.

        Args:
            node_ids: (N,) node indices to embed.
            neighbor_ids: (N, K) neighbor indices per node.

        Returns:
            (N, memory_dim) temporal embeddings.
        """
        query = self.attn_query(self._memory[node_ids])
        if neighbor_ids is None or neighbor_ids.shape[1] == 0:
            return query

        return self._attend_neighbors(query, neighbor_ids)

    def _attend_neighbors(
        self,
        query: torch.Tensor,
        neighbor_ids: torch.Tensor,
    ) -> torch.Tensor:
        """Compute attention-weighted aggregation of neighbor memories."""
        n, k = neighbor_ids.shape
        neighbor_mem = self._memory[neighbor_ids.reshape(-1)]
        keys = self.attn_key(neighbor_mem).view(n, k, self.memory_dim)
        scores = torch.bmm(
            query.unsqueeze(1), keys.transpose(1, 2),
        ).squeeze(1)
        weights = F.softmax(
            scores / (self.memory_dim ** 0.5), dim=-1,
        )
        values = neighbor_mem.view(n, k, self.memory_dim)
        attended = torch.bmm(weights.unsqueeze(1), values).squeeze(1)
        return query + attended

    def save_memory(
        self,
        asset: str,
        memory_dir: Path | None = None,
    ) -> None:
        """Persist memory state to disk via torch.save.

        Args:
            asset: Asset identifier for file naming.
            memory_dir: Directory for memory files.
        """
        out_dir = memory_dir or MEMORY_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "{}.pt".format(asset)
        state = {
            "memory": self._memory.data.clone(),
            "max_nodes": self.max_nodes,
            "memory_dim": self.memory_dim,
        }
        torch.save(state, path)
        logger.info(
            "tgn_memory_saved | asset={} | path={}",
            asset, str(path),
        )

    def load_memory(
        self,
        asset: str,
        memory_dir: Path | None = None,
    ) -> bool:
        """Load memory state from disk — fail-open on error.

        Args:
            asset: Asset identifier for file naming.
            memory_dir: Directory for memory files.

        Returns:
            True if loaded successfully, False if fell back to zeros.
        """
        in_dir = memory_dir or MEMORY_DIR
        path = in_dir / "{}.pt".format(asset)
        if not path.exists():
            logger.debug(
                "tgn_memory_not_found | asset={} | zero_init",
                asset,
            )
            return False
        return self._load_from_file(path, asset)

    def _load_from_file(self, path: Path, asset: str) -> bool:
        """Attempt to load memory file — zero-init on any failure."""
        try:
            state = torch.load(
                path,
                map_location="cpu",
                weights_only=True,
            )
            self._memory.data.copy_(state["memory"])
            logger.info(
                "tgn_memory_loaded | asset={} | path={}",
                asset, str(path),
            )
            return True
        except Exception:
            logger.warning(
                "tgn_memory_corrupt | asset={} | zero_init",
                asset,
            )
            self.reset_memory()
            return False

    def reset_memory(self) -> None:
        """Zero-initialize all memory slots."""
        self._memory.data.zero_()

    def get_memory_state(self) -> torch.Tensor:
        """Return current memory tensor (detached copy)."""
        return self._memory.data.detach().clone()
