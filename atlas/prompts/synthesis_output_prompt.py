"""
Output-format contract for the DeepSeek / local synthesis layer.
Append to system prompts that must emit ``SynthesisOutput`` JSON.
"""

SYNTHESIS_OUTPUT_FORMAT_INSTRUCTIONS = """
You MUST respond with a single JSON object and nothing else.
No explanation, no markdown, no preamble.
The JSON must conform exactly to this schema:
{
  "confluence_total": <integer 0–220>,
  "dimension_breakdown": {
    "derivatives": <integer 0–75>,
    "whale": <integer 0–65>,
    "sentiment": <integer -35 to +35>,
    "macro": <integer 0–30>,
    "technical": <integer 0–15>
  },
  "archetype": <one of: "LIQUIDITY_TRAP", "CAPITULATION_REVERSAL", "DEAD_ZONE", "DERIVATIVES_SKEW", "CONTRADICTION">,
  "decision": <one of: "STRONG_BUY", "BUY", "WEAK_BUY", "STRONG_SELL", "SELL", "WEAK_SELL", "WATCH", "NO_TRADE", "BLOCK">,
  "direction": <"long" | "short" | "none">,
  "confidence_score": <float 0.0–1.0>,
  "active_flags": [<list of string flag names from agents>],
  "provenance": {
    "derivatives_agent": <string>,
    "whale_agent": <string>,
    "sentiment_agent": <string>,
    "macro_agent": <string>,
    "technical_agent": <string>,
    "synthesis_model": <string>
  },
  "latency_ms": <integer>
}
CRITICAL RULES:
- confluence_total MUST equal the sum of all dimension_breakdown values exactly.
- No dimension may exceed its maximum (derivatives=75, whale=65, sentiment=±35, macro=30, technical=15).
- If archetype is CONTRADICTION, decision MUST be NO_TRADE.
- If archetype is LIQUIDITY_TRAP, decision MUST be BLOCK.
- If confidence_score < 0.5, decision MUST be WATCH or NO_TRADE.
""".strip()
