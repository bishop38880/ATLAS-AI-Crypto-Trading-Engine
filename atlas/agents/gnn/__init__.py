"""GNN agent package — shadow-mode graph neural network foundation.

Session GNN-H1: GraphSAGE + GAT + GIN model heads with
MultiGraphCrossAttention fusion. Shadow mode ONLY — GNN scores
are logged and tracked but NEVER added to the live confluence score.
"""

from atlas.agents.gnn.agent import GNNAgent
from atlas.agents.gnn.shadow_scorer import ShadowGNNScorer, ShadowScoreResult

__all__ = ["GNNAgent", "ShadowGNNScorer", "ShadowScoreResult"]
