"""Tests for GNN agent — 6 tests covering shadow mode invariants."""

from __future__ import annotations

import subprocess
import time
from unittest.mock import AsyncMock, MagicMock, patch

import torch
import pytest

from atlas.agents.gnn.agent import GNNAgent
from atlas.agents.gnn.shadow_scorer import ShadowGNNScorer, ShadowScoreResult
from atlas.agents.gnn.models import (
    GATHead,
    GINHead,
    GraphSAGEHead,
    MultiGraphCrossAttention,
)
from atlas.agents.base import AgentResult, SignalDirection


@pytest.fixture
def gnn_agent() -> GNNAgent:
    """Create a GNNAgent with mock settings."""
    with patch("atlas.agents.gnn.agent.PolarisSettings") as mock_cls:
        mock_settings = MagicMock()
        mock_settings.postgres_url = "postgresql://test:test@localhost/test"
        mock_cls.return_value = mock_settings
        mock_pool = AsyncMock()
        mock_redis = AsyncMock()
        agent = GNNAgent(settings=mock_settings, pool=mock_pool, redis_client=mock_redis)
    return agent


class TestGNNAgentZeroWeight:
    """GNNAgent must always return score=0, weight=0.0."""

    @pytest.mark.asyncio
    async def test_returns_zero_score_zero_weight(
        self,
        gnn_agent: GNNAgent,
    ) -> None:
        """GNNAgent returns AgentResult with score=0, weight=0.0."""
        df = {"dummy": 1.0}
        with patch.object(
            gnn_agent._scorer,
            "score_cycle",
            new_callable=AsyncMock,
            return_value=ShadowScoreResult(
                asset="BTCUSDT",
                graphsage_score=0.1,
                gat_score=0.2,
                gin_score=0.3,
                fused_shadow_score=0.5,
                computed_at=__import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc,
                ),
            ),
        ):
            result = await gnn_agent.score(df, {"asset": "BTCUSDT"})

        assert isinstance(result, AgentResult)
        assert result.score == 0
        assert result.weight == 0.0
        assert result.direction == SignalDirection.NEUTRAL
        assert result.max_score == 0


class TestShadowScorerPersistence:
    """Shadow scorer DB write test (mocked)."""

    @pytest.mark.asyncio
    async def test_shadow_scorer_writes_to_db(self) -> None:
        """Shadow scorer calls _persist_result with valid result."""
        mock_settings = MagicMock()
        mock_settings.postgres_url = "postgresql://test:test@localhost/test"
        mock_pool = AsyncMock()
        mock_redis = AsyncMock()
        scorer = ShadowGNNScorer(settings=mock_settings, pool=mock_pool, redis_client=mock_redis)

        with patch.object(
            scorer,
            "_persist_result",
            new_callable=AsyncMock,
        ) as mock_persist:
            result = await scorer.score_cycle(asset="ETHUSDT")

        assert isinstance(result, ShadowScoreResult)
        assert result.asset == "ETHUSDT"
        assert 0.0 <= result.fused_shadow_score <= 1.0
        mock_persist.assert_called_once_with(result)


class TestScorerFailureGraceful:
    """GNNAgent must return zero-weight even when scorer fails."""

    @pytest.mark.asyncio
    async def test_scorer_exception_returns_zero(
        self,
        gnn_agent: GNNAgent,
    ) -> None:
        """Scorer raises → agent still returns score=0, weight=0.0."""
        df = {"dummy": 1.0}
        with patch.object(
            gnn_agent._scorer,
            "score_cycle",
            new_callable=AsyncMock,
            side_effect=RuntimeError("DB down"),
        ):
            result = await gnn_agent.score(df, {"asset": "BTCUSDT"})

        assert result.score == 0
        assert result.weight == 0.0
        assert result.direction == SignalDirection.NEUTRAL


class TestNoLiveScoringReferences:
    """Grep-based invariant: no live scoring in agent.py."""

    def test_no_live_scoring_terms(self) -> None:
        """grep for confluence_score|live_score|add_to_score returns zero."""
        result = subprocess.run(
            [
                "grep", "-rn",
                r"confluence_score\|live_score\|add_to_score",
                "atlas/agents/gnn/agent.py",
            ],
            capture_output=True,
            text=True,
            cwd="/home/bi/Desktop/Engine 8/ATLAS",
        )
        assert result.stdout.strip() == "", (
            "Live scoring references found: {}".format(result.stdout)
        )


class TestNoCudaReferences:
    """Grep-based invariant: no CUDA in entire gnn/ package."""

    def test_no_cuda_calls(self) -> None:
        """grep for cuda|\\.cuda() returns zero across gnn/."""
        result = subprocess.run(
            [
                "grep", "-rn",
                r"cuda\|\.cuda()",
                "atlas/agents/gnn/",
            ],
            capture_output=True,
            text=True,
            cwd="/home/bi/Desktop/Engine 8/ATLAS",
        )
        # Filter out test files that legitimately grep for cuda
        lines = [
            line for line in result.stdout.strip().split("\n")
            if line and "test_" not in line
        ]
        assert len(lines) == 0, (
            "CUDA references found: {}".format("\n".join(lines))
        )


class TestForwardPassLatency:
    """Forward pass benchmark — must complete in < 20ms."""

    def test_forward_under_20ms(self) -> None:
        """Forward pass completes in < 20ms on CPU."""
        from atlas.agents.gnn.graph_builder import (
            build_defi_graph,
            build_token_correlation_graph,
            build_transaction_graph,
        )
        from atlas.agents.gnn.models import (
            GATHead,
            GINHead,
            GraphSAGEHead,
            MultiGraphCrossAttention,
        )

        sage = GraphSAGEHead(in_channels=8)
        gat = GATHead(in_channels=6)
        gin = GINHead(in_channels=4)
        fuser = MultiGraphCrossAttention()

        sage.eval()
        gat.eval()
        gin.eval()
        fuser.eval()

        # Warm up
        _run_forward(sage, gat, gin, fuser)

        # Benchmark
        start = time.perf_counter_ns()
        _run_forward(sage, gat, gin, fuser)
        elapsed_ms = (time.perf_counter_ns() - start) / 1_000_000

        assert elapsed_ms < 20.0, (
            "Forward pass took {:.2f}ms (limit 20ms)".format(elapsed_ms)
        )


def _run_forward(
    sage: GraphSAGEHead,
    gat: GATHead,
    gin: GINHead,
    fuser: MultiGraphCrossAttention,
) -> None:
    """Execute a single forward pass through all heads + fusion."""
    from atlas.agents.gnn.graph_builder import (
        build_defi_graph,
        build_token_correlation_graph,
        build_transaction_graph,
    )

    corr = build_token_correlation_graph(
        [{"log_mcap": 1.0}] * 10,
        [[0.01] * 30] * 10,
    )
    tx = build_transaction_graph(
        [{"log_balance_usd": 1.0}] * 5,
        [(0, 1), (1, 2)],
    )
    defi = build_defi_graph()

    with torch.no_grad():
        e1 = sage(corr)
        e2 = gat(tx)
        e3 = gin(defi)
        fuser(e1, e2, e3)
