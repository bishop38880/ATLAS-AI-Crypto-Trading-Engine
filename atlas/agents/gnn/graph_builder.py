"""Graph constructors for GNN agent — Token Correlation, Transaction, DeFi stub.

Each builder returns a ``torch_geometric.data.Data`` object with:
- ``x``: node feature matrix (float32)
- ``edge_index``: COO edge tensor (int64)
- optional ``edge_attr``

All tensors are CPU float32. Empty-input safety is guaranteed.
"""

from __future__ import annotations

from typing import Any

import torch
from loguru import logger
from torch_geometric.data import Data


# ── Constants ────────────────────────────────────────────────────────
TOKEN_FEATURE_DIM = 8
WALLET_FEATURE_DIM = 6
DEFI_FEATURE_DIM = 4
CORRELATION_THRESHOLD = 0.6


# ── 1. Token Correlation Graph ──────────────────────────────────────


def build_token_correlation_graph(
    asset_features: list[dict[str, float]],
    returns_matrix: list[list[float]],
) -> Data:
    """Build undirected graph where edges connect correlated assets.

    Args:
        asset_features: Per-asset feature dicts with keys:
            log_mcap, log_volume_24h, returns_1h, returns_24h,
            volatility_1h, volatility_24h, rsi_14, atr_normalized.
        returns_matrix: NxT matrix of 1-minute returns per asset
            (N assets, T time steps).

    Returns:
        Data with x (N, 8), edge_index (2, E) symmetric.
    """
    n = len(asset_features)
    if n == 0:
        return _empty_data(TOKEN_FEATURE_DIM)

    x = _extract_token_features(asset_features)
    edge_index = _compute_correlation_edges(returns_matrix, n)
    return Data(x=x, edge_index=edge_index)


def _extract_token_features(
    features: list[dict[str, float]],
) -> torch.Tensor:
    """Extract 8-dim feature vectors from asset dicts."""
    keys = [
        "log_mcap", "log_volume_24h", "returns_1h", "returns_24h",
        "volatility_1h", "volatility_24h", "rsi_14", "atr_normalized",
    ]
    rows: list[list[float]] = []
    for feat in features:
        row = [float(feat.get(k, 0.0)) for k in keys]
        rows.append(row)
    return torch.tensor(rows, dtype=torch.float32)


def _compute_correlation_edges(
    returns_matrix: list[list[float]],
    n: int,
) -> torch.Tensor:
    """Compute undirected edges where |pearson corr| > threshold."""
    if n <= 1 or not returns_matrix:
        return torch.zeros(2, 0, dtype=torch.int64)

    ret = torch.tensor(returns_matrix, dtype=torch.float32)
    corr = _pearson_correlation_matrix(ret)
    src, dst = _threshold_symmetric_edges(corr, n)
    return torch.stack([src, dst], dim=0)


def _pearson_correlation_matrix(ret: torch.Tensor) -> torch.Tensor:
    """Compute NxN Pearson correlation matrix from (N, T) returns."""
    mean = ret.mean(dim=1, keepdim=True)
    centered = ret - mean
    std = centered.std(dim=1, keepdim=True).clamp(min=1e-8)
    normed = centered / std
    corr = torch.mm(normed, normed.t()) / max(ret.shape[1] - 1, 1)
    return corr


def _threshold_symmetric_edges(
    corr: torch.Tensor,
    n: int,
) -> tuple[torch.Tensor, torch.Tensor]:
    """Return symmetric edge pairs where |corr| > CORRELATION_THRESHOLD."""
    src_list: list[int] = []
    dst_list: list[int] = []
    for i in range(n):
        for j in range(i + 1, n):
            if abs(float(corr[i, j])) > CORRELATION_THRESHOLD:
                src_list.extend([i, j])
                dst_list.extend([j, i])
    return (
        torch.tensor(src_list, dtype=torch.int64),
        torch.tensor(dst_list, dtype=torch.int64),
    )


# ── 2. Transaction Graph ────────────────────────────────────────────


def build_transaction_graph(
    wallet_features: list[dict[str, float]],
    transfers: list[tuple[int, int]],
) -> Data:
    """Build directed graph of wallet-to-wallet transfers.

    Args:
        wallet_features: Per-wallet feature dicts with keys:
            log_balance_usd, tx_count_24h, unique_counterparties,
            wallet_age_days, smart_money_score, cex_flow_net_24h.
        transfers: List of (src_wallet_idx, dst_wallet_idx) tuples.

    Returns:
        Data with x (N, 6), edge_index (2, E) directed.
    """
    n = len(wallet_features)
    if n == 0:
        return _empty_data(WALLET_FEATURE_DIM)

    x = _extract_wallet_features(wallet_features)
    edge_index = _build_transfer_edges(transfers)
    return Data(x=x, edge_index=edge_index)


def _extract_wallet_features(
    features: list[dict[str, float]],
) -> torch.Tensor:
    """Extract 6-dim feature vectors from wallet dicts."""
    keys = [
        "log_balance_usd", "tx_count_24h", "unique_counterparties",
        "wallet_age_days", "smart_money_score", "cex_flow_net_24h",
    ]
    rows = [[float(f.get(k, 0.0)) for k in keys] for f in features]
    return torch.tensor(rows, dtype=torch.float32)


def _build_transfer_edges(
    transfers: list[tuple[int, int]],
) -> torch.Tensor:
    """Convert transfer pairs to edge_index tensor."""
    if not transfers:
        return torch.zeros(2, 0, dtype=torch.int64)
    src = [t[0] for t in transfers]
    dst = [t[1] for t in transfers]
    return torch.tensor([src, dst], dtype=torch.int64)


# ── 3. DeFi Protocol Graph (STUB) ───────────────────────────────────


def build_defi_graph() -> Data:
    """Return stub DeFi graph — single dummy node, zero edges.

    DeFi Llama integration lands in Phase 3. Downstream models
    must handle empty graphs gracefully.
    """
    logger.debug("defi_graph_stub_active")
    return Data(
        x=torch.zeros(1, DEFI_FEATURE_DIM, dtype=torch.float32),
        edge_index=torch.zeros(2, 0, dtype=torch.int64),
    )


# ── Helpers ──────────────────────────────────────────────────────────


def _empty_data(feature_dim: int) -> Data:
    """Create a valid Data object with zero nodes and zero edges."""
    return Data(
        x=torch.zeros(0, feature_dim, dtype=torch.float32),
        edge_index=torch.zeros(2, 0, dtype=torch.int64),
    )
