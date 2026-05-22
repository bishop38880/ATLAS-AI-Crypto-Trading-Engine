"""Orchestrator-facing synthesis parse with safe NO_TRADE fallback."""

from __future__ import annotations

from loguru import logger

from atlas.llm.response_parser import parse_synthesis_output
from atlas.schemas.synthesis_output import SynthesisOutput, build_fallback_synthesis_output


def parse_synthesis_or_fallback(
    raw_llm_response: str,
    *,
    symbol: str,
    synthesis_model: str = "unknown",
    latency_ms: int = 0,
) -> SynthesisOutput:
    """
    Parse validated synthesis output or return a zero-score NO_TRADE fallback.

    Never raises — execution paths must not crash on malformed LLM text.
    """
    try:
        return parse_synthesis_output(raw_llm_response)
    except ValueError:
        logger.warning(
            "Synthesis parse failed for symbol={} — defaulting to NO_TRADE",
            symbol,
        )
        return build_fallback_synthesis_output(
            symbol=symbol,
            synthesis_model=synthesis_model,
            latency_ms=latency_ms,
        )
