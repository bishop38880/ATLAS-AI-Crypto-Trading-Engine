"""GNN temporal sub-package — T-HeteroGNN + TGN + Contrastive (H2).

Session GNN-H2: Temporal Graph Networks with persistent memory,
Heterogeneous Graph Transformer attention, and GraphCL contrastive
pretraining. Shadow mode ONLY — scores are logged but NEVER added
to the live confluence score.

H1 models (GraphSAGE, GAT, GIN, MultiGraphCrossAttention) remain
importable and fully functional from ``atlas.agents.gnn.models``.
"""

from atlas.agents.gnn.temporal.t_heterognn import THeteroGNN
from atlas.agents.gnn.temporal.tgn_memory import TGNMemory
from atlas.agents.gnn.temporal.contrastive import (
    ContrastivePretrainer,
    GraphCLAugmenter,
)

__all__ = [
    "THeteroGNN",
    "TGNMemory",
    "ContrastivePretrainer",
    "GraphCLAugmenter",
]
