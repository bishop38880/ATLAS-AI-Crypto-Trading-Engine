"""Graphs-of-Graphs meta-graph layer."""

import torch
from torch import nn


class GoGMetaGraph(nn.Module):
    """Fuses token and transaction graph embeddings into a contagion signal.

    Meta-graph architecture:
    - Node A: token correlation embedding (dim=32)
    - Node B: transaction embedding (dim=32)
    - Fusion via attention, yielding a scalar contagion signal in [0, 1].
    Parameter budget: ~4K. No learned positional encodings.
    """

    def __init__(self, embed_dim: int = 32) -> None:
        super().__init__()
        # PyTorch MultiheadAttention expects sequences. We have seq_len=2 (nodes).
        # Params: embed_dim * embed_dim * 3 (q,k,v) + output projection = ~4K
        self.attention = nn.MultiheadAttention(
            embed_dim=embed_dim,
            num_heads=1,
            batch_first=True,
        )
        self.fc = nn.Linear(embed_dim, 1)

    def forward(
        self, graph_a_emb: torch.Tensor, graph_b_emb: torch.Tensor
    ) -> torch.Tensor:
        """Forward pass fusing two embeddings.

        Args:
            graph_a_emb: Tensor of shape (B, 32).
            graph_b_emb: Tensor of shape (B, 32).

        Returns:
            Contagion signal tensor of shape (B, 1) in [0.0, 1.0].
        """
        # Stack into sequence of 2 tokens. Shape: (B, 2, 32)
        seq = torch.stack([graph_a_emb, graph_b_emb], dim=1)

        # Self-attention over the two nodes. Output shape: (B, 2, 32)
        attn_out, _ = self.attention(seq, seq, seq)

        # Aggregate the nodes via mean pooling. Shape: (B, 32)
        pooled = attn_out.mean(dim=1)

        # Map to scalar contagion signal. Shape: (B, 1)
        out = self.fc(pooled)
        return torch.sigmoid(out)
