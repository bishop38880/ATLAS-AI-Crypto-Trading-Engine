"""Shadow GNN scorer — builds graphs, runs model heads, fuses output.

SHADOW MODE ONLY: Results are logged and written to PostgreSQL but
NEVER published to ``atlas:signals`` and NEVER modify the live
confluence score. No live scoring until explicit human approval.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

import asyncio
import asyncpg
import redis.asyncio as redis
import torch
from loguru import logger
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from torch_geometric.data import Data

from atlas.agents.gnn.graph_builder import (
    build_defi_graph,
    build_token_correlation_graph,
    build_transaction_graph,
)
from atlas.agents.gnn.models import (
    DEFAULT_EMBED_DIM,
    GATHead,
    GINHead,
    GraphSAGEHead,
    MultiGraphCrossAttention,
)
from atlas.agents.gnn.sequential import LiquidNetwork, MambaBlock, SequenceFusion
from atlas.shared.config import PolarisSettings


# ── Result Model ─────────────────────────────────────────────────────


class ShadowScoreResult(BaseModel, frozen=True):
    """Frozen result from shadow GNN scoring cycle.

    All scores are informational — NEVER fed into the live pipeline.
    """

    asset: str
    graphsage_score: float
    gat_score: float
    gin_score: float
    fused_shadow_score: float = Field(ge=0.0, le=1.0)
    mamba_score: float = 0.0
    lnn_score: float = 0.0
    fused_seq_score: float = Field(ge=0.0, le=1.0, default=0.0)
    contagion_signal: float = Field(ge=0.0, le=1.0, default=0.0)
    wavelet_features: list[float] = Field(default_factory=list)
    stress_triggered: bool = False
    computed_at: datetime


# ── Scorer ───────────────────────────────────────────────────────────


class ShadowGNNScorer:
    """Shadow-mode GNN scoring pipeline.

    Builds three graph types, runs three model heads, fuses via
    MultiGraphCrossAttention. Results are persisted to PostgreSQL
    for empirical analysis but NEVER touch the live score.

    Args:
        settings: PolarisSettings for database connectivity.
        embed_dim: Embedding dimension for model heads.
    """

    def __init__(
        self,
        settings: PolarisSettings,
        pool: asyncpg.Pool,
        redis_client: redis.Redis,
        embed_dim: int = DEFAULT_EMBED_DIM,
    ) -> None:
        self._settings = settings
        self._pool = pool
        self._redis = redis_client
        self._embed_dim = embed_dim
        self._sage = GraphSAGEHead(in_channels=8, hidden_dim=embed_dim)
        self._gat = GATHead(in_channels=6, hidden_dim=embed_dim)
        self._gin = GINHead(in_channels=4, hidden_dim=embed_dim)
        self._fuser = MultiGraphCrossAttention(
            embed_dim=embed_dim, num_heads=2,
        )
        self._mamba = MambaBlock(d_model=32, d_state=16, d_conv=4)
        self._lnn = LiquidNetwork(input_dim=32, hidden_dim=16)
        self._seq_fuser = SequenceFusion(gnn_dim=embed_dim, mamba_dim=32, lnn_dim=16)
        from atlas.agents.gnn.advanced import GoGMetaGraph
        self._gog = GoGMetaGraph(embed_dim=embed_dim)
        self._sage.eval()
        self._gat.eval()
        self._gin.eval()
        self._fuser.eval()
        self._mamba.eval()
        self._lnn.eval()
        self._seq_fuser.eval()
        self._gog.eval()

    async def score_cycle(
        self,
        asset: str,
        asset_features: list[dict[str, float]] | None = None,
        returns_matrix: list[list[float]] | None = None,
        wallet_features: list[dict[str, float]] | None = None,
        transfers: list[tuple[int, int]] | None = None,
    ) -> ShadowScoreResult:
        """Run full shadow scoring cycle for a single asset."""
        result, emb_sage, emb_gat, fused_gnn, mamba_out, lnn_out = await asyncio.to_thread(
            self._forward_pass, asset,
            asset_features or [], returns_matrix or [],
            wallet_features or [], transfers or [],
        )
        result = await self._run_sequence_fusion(
            result, fused_gnn, mamba_out, lnn_out,
        )
        await self._maybe_publish_stress(result, asset, emb_sage, emb_gat)
        await self._persist_result(result)
        return result

    async def _run_sequence_fusion(
        self, result: ShadowScoreResult,
        fused_gnn: torch.Tensor, mamba_out: torch.Tensor, lnn_out: torch.Tensor,
    ) -> ShadowScoreResult:
        """Fuse sequential model outputs with GNN embeddings."""
        fused_seq_t = await asyncio.to_thread(self._seq_fuser, fused_gnn, mamba_out, lnn_out)
        return ShadowScoreResult(
            asset=result.asset, graphsage_score=result.graphsage_score,
            gat_score=result.gat_score, gin_score=result.gin_score,
            fused_shadow_score=result.fused_shadow_score,
            mamba_score=float(mamba_out.mean().item()),
            lnn_score=float(lnn_out.mean().item()),
            fused_seq_score=float(fused_seq_t.item()),
            contagion_signal=result.contagion_signal,
            wavelet_features=result.wavelet_features,
            stress_triggered=result.stress_triggered,
            computed_at=result.computed_at,
        )

    async def _maybe_publish_stress(
        self, result: ShadowScoreResult, asset: str,
        emb_sage: torch.Tensor, emb_gat: torch.Tensor,
    ) -> None:
        """Publish stress trigger if contagion exceeds threshold."""
        if not result.stress_triggered:
            return
        from atlas.agents.gnn.advanced import publish_stress_trigger
        await publish_stress_trigger(
            asset=asset, contagion_signal=result.contagion_signal,
            threshold=0.75, graph_a_emb=emb_sage, graph_b_emb=emb_gat,
            redis_client=self._redis,
        )

    def _build_sequence_tensor(self, returns_matrix: list[list[float]]) -> torch.Tensor:
        seq_x = torch.tensor(returns_matrix, dtype=torch.float32)
        if seq_x.numel() == 0:
            return torch.zeros(1, 1, 32)
        if seq_x.dim() == 1:
            seq_x = seq_x.unsqueeze(0).unsqueeze(-1)
        elif seq_x.dim() == 2:
            seq_x = seq_x.T.unsqueeze(0)
        
        seq_len = min(seq_x.size(1), 256)
        seq_x = seq_x[:, :seq_len, :]
        feats = seq_x.size(2)
        if feats < 32:
            return torch.cat([seq_x, torch.zeros(1, seq_len, 32 - feats)], dim=-1)
        return seq_x[:, :, :32]

    def _forward_pass(
        self,
        asset: str,
        asset_features: list[dict[str, float]],
        returns_matrix: list[list[float]],
        wallet_features: list[dict[str, float]],
        transfers: list[tuple[int, int]],
    ) -> tuple[ShadowScoreResult, torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
        """Pure torch forward pass — no async, no I/O."""
        corr_graph = build_token_correlation_graph(
            asset_features, returns_matrix,
        )
        tx_graph = build_transaction_graph(wallet_features, transfers)
        defi_graph = build_defi_graph()
        return self._run_heads_and_fuse(
            asset, corr_graph, tx_graph, defi_graph, returns_matrix
        )

    @torch.no_grad()
    def _run_heads_and_fuse(
        self,
        asset: str,
        corr_graph: Data,
        tx_graph: Data,
        defi_graph: Data,
        returns_matrix: list[list[float]],
    ) -> tuple[ShadowScoreResult, torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
        """Run model heads and fuse embeddings."""
        emb_sage = self._sage(corr_graph)
        emb_gat = self._gat(tx_graph)
        emb_gin = self._gin(defi_graph)
        fused = self._fuser(emb_sage, emb_gat, emb_gin)
        fused_score = float(torch.sigmoid(fused.mean()).item())
        seq_x = self._build_seq_tensor(returns_matrix)
        mamba_out = self._mamba(seq_x)
        lnn_out = self._lnn(seq_x)
        result = self._build_gnn_result(
            asset, emb_sage, emb_gat, emb_gin, fused_score, returns_matrix,
        )
        return result, emb_sage, emb_gat, fused, mamba_out, lnn_out

    @staticmethod
    def _build_seq_tensor(returns_matrix: list[list[float]]) -> torch.Tensor:
        """Build padded sequence tensor for Mamba/LNN."""
        seq_x = torch.tensor(returns_matrix, dtype=torch.float32)
        if seq_x.numel() == 0:
            return torch.zeros(1, 1, 32)
        if seq_x.dim() == 1:
            seq_x = seq_x.unsqueeze(0).unsqueeze(-1)
        elif seq_x.dim() == 2:
            seq_x = seq_x.T.unsqueeze(0)
        seq_len = min(seq_x.size(1), 256)
        seq_x = seq_x[:, :seq_len, :]
        feats = seq_x.size(2)
        if feats < 32:
            seq_x = torch.cat([seq_x, torch.zeros(1, seq_len, 32 - feats)], dim=-1)
        else:
            seq_x = seq_x[:, :, :32]
        return seq_x

    def _build_gnn_result(
        self, asset: str,
        emb_sage: torch.Tensor, emb_gat: torch.Tensor, emb_gin: torch.Tensor,
        fused_score: float, returns_matrix: list[list[float]],
    ) -> ShadowScoreResult:
        """Build the base ShadowScoreResult with GNN + advanced features."""
        contagion_t = self._gog(emb_sage, emb_gat)
        contagion_signal = float(contagion_t.mean().item())
        from atlas.agents.gnn.advanced import extract_wavelet_features
        ts = returns_matrix[0] if returns_matrix else []
        wavelet_features = extract_wavelet_features(ts).tolist()
        return ShadowScoreResult(
            asset=asset,
            graphsage_score=float(emb_sage.mean().item()),
            gat_score=float(emb_gat.mean().item()),
            gin_score=float(emb_gin.mean().item()),
            fused_shadow_score=max(0.0, min(1.0, fused_score)),
            contagion_signal=contagion_signal,
            wavelet_features=wavelet_features,
            stress_triggered=contagion_signal > 0.75,
            computed_at=datetime.now(timezone.utc),
        )

    async def _persist_result(self, result: ShadowScoreResult) -> None:
        """Write shadow score to PostgreSQL via injected pool and Redis."""
        try:
            async with self._pool.acquire() as conn:
                await self._execute_insert(conn, result)
            
            import msgspec
            payload = msgspec.json.encode({
                "asset": result.asset,
                "graphsage_score": result.graphsage_score,
                "gat_score": result.gat_score,
                "gin_score": result.gin_score,
                "fused_shadow_score": result.fused_shadow_score,
                "mamba_score": result.mamba_score,
                "lnn_score": result.lnn_score,
                "fused_seq_score": result.fused_seq_score,
                "contagion_signal": result.contagion_signal,
                "stress_triggered": result.stress_triggered,
                "computed_at": result.computed_at.isoformat(),
            })
            redis_key = f"polaris:gnn:{result.asset}:latest"
            await self._redis.setex(redis_key, 86400, payload)
        except Exception as e:
            logger.error(
                "shadow_gnn_persist_failed | asset={} | score={} | error={}",
                result.asset,
                result.fused_shadow_score,
                str(e),
            )

    async def _execute_insert(
        self,
        conn: asyncpg.Connection,
        result: ShadowScoreResult,
    ) -> None:
        """Execute INSERT into shadow_gnn_scores and shadow_gnn_scores_advanced."""
        await self._insert_h1(conn, result)
        await self._insert_h4(conn, result)
        logger.info(
            "shadow_gnn_score_persisted | asset={} | fused={} | seq_fused={}",
            result.asset, result.fused_shadow_score, result.fused_seq_score,
        )

    @staticmethod
    async def _insert_h1(conn: asyncpg.Connection, r: ShadowScoreResult) -> None:
        """Insert into the H1 shadow_gnn_scores table."""
        q = (
            "INSERT INTO shadow_gnn_scores "
            "(asset, graphsage_score, gat_score, gin_score, fused_shadow_score, "
            "mamba_score, lnn_score, fused_seq_score, computed_at) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)"
        )
        try:
            await conn.execute(  # type: ignore[union-attr]
                q, r.asset, r.graphsage_score, r.gat_score, r.gin_score,
                r.fused_shadow_score, r.mamba_score, r.lnn_score,
                r.fused_seq_score, r.computed_at,
            )
        except Exception as e:
            logger.error("shadow_gnn_scores H1 insert skipped/failed | error={}", str(e))

    @staticmethod
    async def _insert_h4(conn: asyncpg.Connection, r: ShadowScoreResult) -> None:
        """Insert into the H4 shadow_gnn_scores_advanced table."""
        import msgspec
        q = (
            "INSERT INTO shadow_gnn_scores_advanced "
            "(asset, contagion_signal, wavelet_features, stress_triggered, computed_at) "
            "VALUES ($1, $2, $3, $4, $5)"
        )
        await conn.execute(  # type: ignore[union-attr]
            q, r.asset, r.contagion_signal,
            msgspec.json.encode(r.wavelet_features).decode("utf-8"),
            r.stress_triggered, r.computed_at,
        )
