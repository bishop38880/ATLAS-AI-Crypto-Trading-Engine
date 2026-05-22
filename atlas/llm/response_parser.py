"""Parse and validate LLM synthesis JSON (Mistral Nemo / DeepSeek)."""

from __future__ import annotations

import re

import msgspec
from loguru import logger

from atlas.schemas.synthesis_output import SynthesisOutput

_THINK_BLOCK_RE = re.compile(
    r"<think>.*?</think>",
    re.DOTALL,
)
_JSON_FENCE_RE = re.compile(r"```json\s*(.*?)\s*```", re.DOTALL)


def strip_think_blocks(raw: str) -> str:
    """Strip ``<think>`` blocks produced by Mistral Nemo 12B."""
    return _THINK_BLOCK_RE.sub("", raw).strip()


def extract_json_from_response(raw: str) -> str:
    """Strip think blocks and markdown fences; return raw JSON string."""
    cleaned = strip_think_blocks(raw)
    fence_match = _JSON_FENCE_RE.search(cleaned)
    if fence_match:
        return fence_match.group(1).strip()
    return cleaned.strip()


def parse_synthesis_output(raw_llm_response: str) -> SynthesisOutput:
    """
    Parse raw LLM response into validated SynthesisOutput.

    Strips think blocks, decodes via msgspec, then validates via Pydantic.
    Raises ValueError on parse failure — caller must handle gracefully.
    """
    json_str = extract_json_from_response(raw_llm_response)
    try:
        raw_dict = msgspec.json.decode(json_str.encode())
        output = SynthesisOutput.model_validate(raw_dict)
        logger.info(
            "Synthesis parsed: total={} archetype={} decision={} confidence={}",
            output.confluence_total,
            output.archetype.value,
            output.decision.value,
            output.confidence_score,
        )
        return output
    except Exception as exc:
        logger.warning("Synthesis parse failed: {}", exc)
        raise ValueError("SynthesisOutput parse failed: {}".format(exc)) from exc
