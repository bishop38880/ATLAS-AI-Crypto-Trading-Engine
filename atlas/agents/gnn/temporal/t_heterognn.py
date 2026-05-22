"""T-HeteroGNN — Temporal Heterogeneous GNN with HGT attention.

Implements Heterogeneous Graph Transformer (Hu et al., 2020) with:
- 2 HGT layers, 4 attention heads, hidden_dim=32
- Node types: asset, wallet, protocol
- Edge types: correlates_with, transfers_to, stakes_in
- Per-node-type linear projections (Q/K/V)
- Per-edge-type learnable relation embeddings

CPU float32 only. No CUDA. Shadow mode — NEVER touches live scoring.
"""

from __future__ import annotations

import math
from pathlib import Path

import torch
import torch.nn as nn
import torch.nn.functional as F
from loguru import logger
from torch_geometric.data import HeteroData


# ── Constants ────────────────────────────────────────────────────────

NODE_TYPES: list[str] = ["asset", "wallet", "protocol"]
EDGE_TYPES: list[tuple[str, str, str]] = [
    ("asset", "correlates_with", "asset"),
    ("wallet", "transfers_to", "wallet"),
    ("protocol", "stakes_in", "protocol"),
]
DEFAULT_FEATURE_DIMS: dict[str, int] = {
    "asset": 8,
    "wallet": 6,
    "protocol": 4,
}
DEFAULT_HIDDEN_DIM = 32
DEFAULT_NUM_HEADS = 4
DEFAULT_NUM_LAYERS = 2
PRETRAINED_PATH = Path("shadow/pretrained/hgt_contrastive.pt")


# ── HGT Layer ────────────────────────────────────────────────────────


class HGTLayer(nn.Module):
    """Single Heterogeneous Graph Transformer layer.

    Implements type-specific Q/K/V projections and relation-parameterized
    multi-head attention per Hu et al., 2020.

    Args:
        hidden_dim: Hidden dimension (shared across types).
        num_heads: Number of attention heads.
        node_types: List of node type names.
        edge_types: List of (src_type, rel_type, dst_type) tuples.
    """

    def __init__(
        self,
        hidden_dim: int = DEFAULT_HIDDEN_DIM,
        num_heads: int = DEFAULT_NUM_HEADS,
        node_types: list[str] | None = None,
        edge_types: list[tuple[str, str, str]] | None = None,
    ) -> None:
        super().__init__()
        self.hidden_dim = hidden_dim
        self.num_heads = num_heads
        self.head_dim = hidden_dim // num_heads
        self._node_types = node_types or NODE_TYPES
        self._edge_types = edge_types or EDGE_TYPES
        self._init_projections()
        self._init_relation_params()

    def _init_projections(self) -> None:
        """Initialize per-node-type Q/K/V linear projections."""
        self.q_proj = nn.ModuleDict({
            nt: nn.Linear(self.hidden_dim, self.hidden_dim, bias=False)
            for nt in self._node_types
        })
        self.k_proj = nn.ModuleDict({
            nt: nn.Linear(self.hidden_dim, self.hidden_dim, bias=False)
            for nt in self._node_types
        })
        self.v_proj = nn.ModuleDict({
            nt: nn.Linear(self.hidden_dim, self.hidden_dim, bias=False)
            for nt in self._node_types
        })
        self.output_proj = nn.ModuleDict({
            nt: nn.Linear(self.hidden_dim, self.hidden_dim)
            for nt in self._node_types
        })
        self.layer_norms = nn.ModuleDict({
            nt: nn.LayerNorm(self.hidden_dim)
            for nt in self._node_types
        })

    def _init_relation_params(self) -> None:
        """Initialize per-edge-type relation attention matrices."""
        self.relation_attn = nn.ParameterDict()
        self.relation_msg = nn.ParameterDict()
        for src, rel, dst in self._edge_types:
            key = "{}__{}_{}".format(src, rel, dst)
            self.relation_attn[key] = nn.Parameter(
                torch.randn(self.num_heads, self.head_dim, self.head_dim)
                * 0.01,
            )
            self.relation_msg[key] = nn.Parameter(
                torch.randn(self.num_heads, self.head_dim, self.head_dim)
                * 0.01,
            )

    def forward(
        self,
        x_dict: dict[str, torch.Tensor],
        data: HeteroData,
    ) -> dict[str, torch.Tensor]:
        """Forward pass: HGT attention over all edge types.

        Args:
            x_dict: Per-node-type feature tensors.
            data: HeteroData with edge_index per edge type.

        Returns:
            Updated per-node-type feature dict.
        """
        out_dict: dict[str, list[torch.Tensor]] = {
            nt: [] for nt in self._node_types
        }
        for src_type, rel_type, dst_type in self._edge_types:
            result = self._compute_attention_for_edge_type(
                x_dict, data, src_type, rel_type, dst_type,
            )
            if result is not None:
                out_dict[dst_type].append(result)

        return self._aggregate_and_residual(x_dict, out_dict)

    def _compute_attention_for_edge_type(
        self,
        x_dict: dict[str, torch.Tensor],
        data: HeteroData,
        src_type: str,
        rel_type: str,
        dst_type: str,
    ) -> torch.Tensor | None:
        """Compute HGT attention for a single edge type.

        Returns None if edge type is empty or missing.
        """
        edge_key = (src_type, rel_type, dst_type)
        if edge_key not in data.edge_types:
            return None

        edge_store = data[edge_key]
        if not hasattr(edge_store, "edge_index"):
            return None

        edge_index = edge_store.edge_index
        if edge_index.shape[1] == 0:
            return None

        if src_type not in x_dict or dst_type not in x_dict:
            return None

        return self._attention_forward(
            x_dict[src_type], x_dict[dst_type],
            edge_index, src_type, rel_type, dst_type,
        )

    def _attention_forward(
        self,
        x_src: torch.Tensor,
        x_dst: torch.Tensor,
        edge_index: torch.Tensor,
        src_type: str,
        rel_type: str,
        dst_type: str,
    ) -> torch.Tensor:
        """Multi-head relation-parameterized attention."""
        src_idx, dst_idx = edge_index[0], edge_index[1]
        q = self.q_proj[dst_type](x_dst)
        k = self.k_proj[src_type](x_src)
        v = self.v_proj[src_type](x_src)

        n_dst = x_dst.shape[0]
        q = q.view(-1, self.num_heads, self.head_dim)
        k = k.view(-1, self.num_heads, self.head_dim)
        v = v.view(-1, self.num_heads, self.head_dim)

        rel_key = "{}__{}_{}".format(src_type, rel_type, dst_type)
        return self._scatter_attention(
            q, k, v, src_idx, dst_idx, n_dst, rel_key,
        )

    def _scatter_attention(
        self,
        q: torch.Tensor,
        k: torch.Tensor,
        v: torch.Tensor,
        src_idx: torch.Tensor,
        dst_idx: torch.Tensor,
        n_dst: int,
        rel_key: str,
    ) -> torch.Tensor:
        """Scatter-based attention aggregation to destination nodes."""
        q_dst = q[dst_idx]
        k_src = k[src_idx]
        v_src = v[src_idx]

        w_attn = self.relation_attn[rel_key]
        k_rel = torch.einsum("ehd,hdk->ehk", k_src, w_attn)
        attn_raw = (q_dst * k_rel).sum(dim=-1) / math.sqrt(self.head_dim)

        attn_scores = _scatter_softmax(attn_raw, dst_idx, n_dst)

        w_msg = self.relation_msg[rel_key]
        v_rel = torch.einsum("ehd,hdk->ehk", v_src, w_msg)
        weighted = v_rel * attn_scores.unsqueeze(-1)

        out = torch.zeros(
            n_dst, self.num_heads, self.head_dim,
            dtype=weighted.dtype,
        )
        out.scatter_add_(0, dst_idx.unsqueeze(1).unsqueeze(2).expand_as(weighted), weighted)
        return out.view(n_dst, self.hidden_dim)

    def _aggregate_and_residual(
        self,
        x_dict: dict[str, torch.Tensor],
        out_dict: dict[str, list[torch.Tensor]],
    ) -> dict[str, torch.Tensor]:
        """Aggregate attention outputs and apply residual + LayerNorm."""
        result: dict[str, torch.Tensor] = {}
        for nt in self._node_types:
            if nt not in x_dict:
                continue
            x = x_dict[nt]
            messages = out_dict.get(nt, [])
            if messages:
                agg = torch.stack(messages, dim=0).mean(dim=0)
                proj = self.output_proj[nt](agg)
                x = self.layer_norms[nt](x + F.relu(proj))
            result[nt] = x
        return result


# ── T-HeteroGNN ──────────────────────────────────────────────────────


class THeteroGNN(nn.Module):
    """Temporal Heterogeneous GNN with HGT attention.

    Stacks ``num_layers`` HGT layers with per-node-type input
    projections. Optionally loads pretrained contrastive weights.

    Args:
        feature_dims: Per-node-type input feature dimensions.
        hidden_dim: Hidden dimension for all layers.
        num_heads: Number of attention heads per layer.
        num_layers: Number of HGT layers.
        pretrained_path: Path to pretrained encoder weights.
    """

    def __init__(
        self,
        feature_dims: dict[str, int] | None = None,
        hidden_dim: int = DEFAULT_HIDDEN_DIM,
        num_heads: int = DEFAULT_NUM_HEADS,
        num_layers: int = DEFAULT_NUM_LAYERS,
        pretrained_path: Path | None = None,
    ) -> None:
        super().__init__()
        self.hidden_dim = hidden_dim
        self._feature_dims = feature_dims or DEFAULT_FEATURE_DIMS
        self.input_proj = nn.ModuleDict({
            nt: nn.Linear(dim, hidden_dim)
            for nt, dim in self._feature_dims.items()
        })
        self.layers = nn.ModuleList([
            HGTLayer(hidden_dim, num_heads)
            for _ in range(num_layers)
        ])
        self._try_load_pretrained(pretrained_path or PRETRAINED_PATH)

    def forward(self, data: HeteroData) -> dict[str, torch.Tensor]:
        """Forward pass through HGT layers.

        Args:
            data: HeteroData with node features per type.

        Returns:
            Per-node-type embedding dict.
        """
        x_dict = self._project_inputs(data)
        for layer in self.layers:
            x_dict = layer(x_dict, data)
        return x_dict

    def _project_inputs(
        self,
        data: HeteroData,
    ) -> dict[str, torch.Tensor]:
        """Project raw node features to hidden_dim per type."""
        x_dict: dict[str, torch.Tensor] = {}
        for nt in self._feature_dims:
            if nt in data.node_types and hasattr(data[nt], "x"):
                node_x = data[nt].x
                if node_x is not None and node_x.shape[0] > 0:
                    x_dict[nt] = self.input_proj[nt](node_x)
        return x_dict

    def _try_load_pretrained(self, path: Path) -> None:
        """Load pretrained weights if available — fail-open."""
        if not path.exists():
            logger.debug(
                "no_pretrained_weights | path={}",
                str(path),
            )
            return
        try:
            state = torch.load(
                path,
                map_location="cpu",
                weights_only=True,
            )
            self.load_state_dict(state, strict=False)
            logger.info(
                "pretrained_weights_loaded | path={}",
                str(path),
            )
        except Exception:
            logger.warning(
                "pretrained_weights_failed | path={}",
                str(path),
            )


# ── Helpers ──────────────────────────────────────────────────────────


def _scatter_softmax(
    src: torch.Tensor,
    index: torch.Tensor,
    num_nodes: int,
) -> torch.Tensor:
    """Compute softmax over groups defined by index.

    Args:
        src: (E, H) raw attention scores.
        index: (E,) destination node indices.
        num_nodes: Total number of destination nodes.

    Returns:
        (E, H) softmaxed attention weights summing to 1 per node.
    """
    idx_exp = index.unsqueeze(-1).expand_as(src)
    max_vals = torch.full(
        (num_nodes, src.shape[1]), float("-inf"), dtype=src.dtype,
    )
    max_vals.scatter_reduce_(0, idx_exp, src, reduce="amax", include_self=True)
    src_stable = src - max_vals.gather(0, idx_exp)
    exp_src = torch.exp(src_stable)
    sum_exp = torch.zeros(num_nodes, src.shape[1], dtype=src.dtype)
    sum_exp.scatter_add_(0, idx_exp, exp_src)
    denom = sum_exp.gather(0, idx_exp).clamp(min=1e-12)
    return exp_src / denom
