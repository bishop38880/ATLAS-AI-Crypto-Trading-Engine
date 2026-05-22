"""CARL framework 'Context' layer assembler.

This module converts agent outputs and multi-timeframe concordance data
into a structured prompt block for the DeepSeek orchestrator.
"""

from typing import Any

import msgspec
from loguru import logger

from atlas.agents.base import AgentResult

REQUIRED_TIMEFRAMES = ["15m", "30m", "1h", "4h", "1d", "1w"]

class ContextAssembler:
    """Assembles agent outputs into a structured prompt block."""

    @staticmethod
    def build_matrix_string(
        asset: str,
        current_score: int,
        agent_results: list[AgentResult],
        mtf_context: dict[str, str],
    ) -> str:
        """Collapse agent outputs and multi-timeframe concordance data.
        
        Returns:
            Prompt block ready for injection into the DeepSeek system prompt.
        """
        mtf_display = {tf: mtf_context.get(tf, "Unknown") for tf in REQUIRED_TIMEFRAMES}
        matrix = ContextAssembler._build_sub_signal_matrix(agent_results)
        matrix_json = msgspec.json.encode(matrix).decode("utf-8")
        mtf_block = ContextAssembler._build_mtf_block(mtf_display)

        logger.debug(
            "context matrix built | asset={} | score={} | agent_count={}",
            asset,
            current_score,
            len(agent_results),
        )

        return (
            f"ASSET: {asset}\n"
            f"CURRENT CONFLUENCE SCORE: {current_score}/220\n\n"
            f"MULTI-TIMEFRAME CONCORDANCE:\n{mtf_block}\n"
            f"AGENT SUB-SIGNAL MATRIX:\n{matrix_json}"
        )

    @staticmethod
    def _build_sub_signal_matrix(
        agent_results: list[AgentResult],
    ) -> dict[str, dict[str, Any]]:
        """Collapse agent results into a JSON-serialisable matrix."""
        out: dict[str, dict[str, Any]] = {}
        for ar in agent_results:
            out[ar.agent_name] = {
                "score_contribution": ar.score,
                "weight": ar.weight,
                "signals": {k: v.model_dump() for k, v in ar.sub_signals.items()},
            }
        return out

    @staticmethod
    def _build_mtf_block(mtf_display: dict[str, str]) -> str:
        """Render timeframe concordance as a bulleted list block."""
        return "".join(f"- {tf}: {state}\n" for tf, state in mtf_display.items())
