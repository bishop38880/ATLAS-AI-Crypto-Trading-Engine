import torch
import torch.nn as nn


class SequenceFusion(nn.Module):
    """Fuses Mamba, LNN, and GNN embeddings into a single shadow score."""

    def __init__(self, gnn_dim: int, mamba_dim: int, lnn_dim: int) -> None:
        super().__init__()
        fusion_dim = max(gnn_dim, mamba_dim, lnn_dim)

        self.gnn_proj = nn.Linear(gnn_dim, fusion_dim)
        self.mamba_proj = nn.Linear(mamba_dim, fusion_dim)
        self.lnn_proj = nn.Linear(lnn_dim, fusion_dim)

        self.attn = nn.MultiheadAttention(
            embed_dim=fusion_dim,
            num_heads=2,
            batch_first=True,
        )

        self.score_proj = nn.Sequential(
            nn.Linear(fusion_dim, fusion_dim // 2),
            nn.SiLU(),
            nn.Linear(fusion_dim // 2, 1),
            nn.Sigmoid(),
        )

    def forward(
        self,
        gnn_emb: torch.Tensor,
        mamba_out: torch.Tensor,
        lnn_out: torch.Tensor,
    ) -> torch.Tensor:
        """Returns a scalar shadow score in [0, 1]."""
        q = self.gnn_proj(gnn_emb).unsqueeze(1)

        m_pool = mamba_out.mean(dim=1)
        l_pool = lnn_out.mean(dim=1)

        k_m = self.mamba_proj(m_pool).unsqueeze(1)
        k_l = self.lnn_proj(l_pool).unsqueeze(1)
        kv = torch.cat([k_m, k_l], dim=1)

        attn_out, _ = self.attn(query=q, key=kv, value=kv)

        score = self.score_proj(attn_out.squeeze(1))
        return score
