"""Local-model helpers: evaluation fixtures and prompts for LM Studio / llama.cpp."""

from __future__ import annotations

from atlas.llm.production_packets.loader import (
    load_production_packets_v3,
    production_packets_v3_path,
)
from atlas.llm.response_parser import (
    extract_json_from_response,
    parse_synthesis_output,
    strip_think_blocks,
)
from atlas.llm.synthesis_evaluator import parse_synthesis_or_fallback

__all__ = [
    "extract_json_from_response",
    "load_production_packets_v3",
    "parse_synthesis_or_fallback",
    "parse_synthesis_output",
    "production_packets_v3_path",
    "strip_think_blocks",
]
