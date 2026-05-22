"""Context Assembler — builds the full LLM context window.

S3-P7 canonical implementation. This is the last step before the LLM Router.
Collects RAG historical context, live provider snapshots, and agent verdicts
into a single structured prompt with token-budget gating.

Architecture:
    ATLAS reads PROMETHEUS-published state via Redis (read-only).
    The context assembler never writes PROMETHEUS keys.
    System prompt encodes POLARIS identity, scoring framework, and hard limits.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field
from redis.asyncio import Redis

from atlas.models.signal import AgentResult
from atlas.pipeline.query_classifier import QueryClassification
from atlas.rag.pipeline import RAGPipeline
from atlas.scoring.scoring_weights import (
    AGENT_VERDICT_CATEGORY_MAX_POINTS,
    AGENT_VERDICT_CATEGORY_ORDER,
    CATEGORY_MAX_POINTS,
)
from atlas.prompts.synthesis_output_prompt import SYNTHESIS_OUTPUT_FORMAT_INSTRUCTIONS
from atlas.shared.config import PolarisSettings

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_RAG_ROUTES: frozenset[str] = frozenset({"RAG_ONLY", "HYBRID"})
_LIVE_ROUTES: frozenset[str] = frozenset({"MCP_ONLY", "HYBRID"})

# Token estimation multiplier: ~1.3 tokens per whitespace-delimited word.
_TOKENS_PER_WORD: float = 1.3


# ---------------------------------------------------------------------------
# AssembledContext — frozen output model
# ---------------------------------------------------------------------------


class AssembledContext(BaseModel):
    """Immutable assembled context for the LLM Router."""

    model_config = ConfigDict(frozen=True)

    system_prompt: str
    user_message: str
    context_sections: dict[str, str] = Field(default_factory=dict)
    rag_documents_used: int = 0
    estimated_tokens: int = 0
    route_used: str
    asset: str
    assembly_timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# ContextAssembler
# ---------------------------------------------------------------------------


class ContextAssembler:
    """Assembles the full context window for the LLM call.

    Merges RAG results, live provider data, agent verdicts, and system
    context into a single structured prompt with token-budget gating.

    Args:
        settings: PolarisSettings instance.
        rag_pipeline: RAGPipeline for historical context retrieval.
        redis_client: ``redis.asyncio.Redis`` connection (read-only).
        max_context_tokens: Hard token budget (default 8000).
    """

    def __init__(
        self,
        settings: PolarisSettings,
        rag_pipeline: RAGPipeline,
        redis_client: Redis,  # type: ignore[type-arg]
        max_context_tokens: int = 8000,
    ) -> None:
        """Initialize ContextAssembler with all dependencies."""
        self._settings = settings
        self._rag = rag_pipeline
        self._redis = redis_client
        self._max_tokens = max_context_tokens

    # ── Public API ────────────────────────────────────────────────────

    async def assemble(
        self,
        query: str,
        classification: QueryClassification,
        agent_results: list[AgentResult],
        provider_snapshots: dict[str, dict[str, Any]],
        asset: str,
    ) -> AssembledContext:
        """Assemble full context for the LLM Router.

        Steps:
            a. Build system context (identity + hard limits).
            b. Fetch RAG context (RAG_ONLY or HYBRID routes only).
            c. Format live provider data (MCP_ONLY or HYBRID only).
            d. Format agent verdicts (always present).
            e. Build user message from sections.
            f. Estimate tokens; truncate if over budget.

        Returns:
            Frozen AssembledContext ready for the LLM Router.
        """
        system_section = self._build_system_context(asset)
        sections = await self._gather_sections(
            query, asset, classification.route,
            agent_results, provider_snapshots,
        )
        user_message, estimated = self._apply_token_budget(
            query, system_section, sections,
        )

        rag_doc_count = int(sections.pop("rag_count", "0"))

        return AssembledContext(
            system_prompt=system_section,
            user_message=user_message,
            context_sections=sections,
            rag_documents_used=rag_doc_count,
            estimated_tokens=estimated,
            route_used=classification.route,
            asset=asset,
        )

    async def _gather_sections(
        self,
        query: str,
        asset: str,
        route: str,
        agent_results: list[AgentResult],
        provider_snapshots: dict[str, dict[str, Any]],
    ) -> dict[str, str]:
        """Gather RAG, live, and agent sections into a dict."""
        rag_section, rag_count = await self._fetch_rag_context_if_needed(
            query, asset, route,
        )
        live_section = self._format_live_data_if_needed(
            provider_snapshots, asset, route,
        )
        agents_section = _format_agent_verdicts(agent_results)
        return {
            "rag": rag_section,
            "live": live_section,
            "agents": agents_section,
            "rag_count": str(rag_count),
        }

    def _apply_token_budget(
        self,
        query: str,
        system_section: str,
        sections: dict[str, str],
    ) -> tuple[str, int]:
        """Build user message and apply token truncation if needed.

        When truncation occurs, ``sections["rag"]`` is updated to
        reflect the truncated content so ``context_sections`` matches
        the actual user message sent to the LLM.
        """
        rag = sections.get("rag", "")
        live = sections.get("live", "")
        agents = sections.get("agents", "")

        user_message = _build_user_message(query, rag, live, agents)
        estimated = _estimate_tokens(system_section + user_message)

        if estimated > self._max_tokens:
            truncated_rag = _compute_truncated_rag(
                query, rag, live, agents,
                self._max_tokens, system_section,
            )
            sections["rag"] = truncated_rag
            user_message = _build_user_message(
                query, truncated_rag, live, agents,
            )
            estimated = _estimate_tokens(system_section + user_message)

        return user_message, estimated

    # ── System context ────────────────────────────────────────────────

    def _build_system_context(self, asset: str) -> str:
        """Return POLARIS identity, hard limits, and scoring skeleton."""
        return (
            "You are POLARIS, a multi-agent cryptocurrency trading "
            "intelligence platform.\n"
            f"Asset under analysis: {asset}.\n"
            "HARD LIMITS:\n"
            "- ATLAS is intelligence-only — no execution, no position writes.\n"
            "- 220-point confluence (five pillars: derivatives 75, whale/on-chain 65,"
            " sentiment 35, macro/regime 30, technical filter 15)."
            "- All financial values use Decimal precision.\n"
            "- Signal Schema v2 fields: decision, confidence, category_scores,"
            " action (side/price/stop_loss/take_profit), expires_at.\n"
            "- DeepSeek is the sole LLM. No other provider references.\n"
            "Provide a structured analysis with decision, confidence, "
            "key convergences, key risks, and reasoning.\n\n"
            + SYNTHESIS_OUTPUT_FORMAT_INSTRUCTIONS
        )

    # ── RAG context ───────────────────────────────────────────────────

    async def _fetch_rag_context_if_needed(
        self,
        query: str,
        asset: str,
        route: str,
    ) -> tuple[str, int]:
        """Fetch RAG context only for RAG_ONLY / HYBRID routes."""
        if route not in _RAG_ROUTES:
            return ("", 0)
        return await self._fetch_rag_context(query, asset)

    async def _fetch_rag_context(
        self,
        query: str,
        asset: str,
        top_n: int = 5,
    ) -> tuple[str, int]:
        """Query RAG pipeline and format results.

        Returns:
            Tuple of (formatted_section, document_count).
            On failure: descriptive error message and 0 docs.
        """
        try:
            documents = await self._rag.query_context(query, asset, top_n)
        except Exception as exc:
            logger.error(
                "RAG context fetch failed | asset={} | error={}",
                asset,
                str(exc),
            )
            return ("Historical context unavailable (pipeline error).", 0)

        if not documents:
            return ("HISTORICAL CONTEXT: No relevant documents found.", 0)

        return (_format_rag_documents(documents), len(documents))

    # ── Live data ─────────────────────────────────────────────────────

    def _format_live_data_if_needed(
        self,
        provider_snapshots: dict[str, dict[str, Any]],
        asset: str,
        route: str,
    ) -> str:
        """Format live data only for MCP_ONLY / HYBRID routes."""
        if route not in _LIVE_ROUTES:
            return ""
        return _format_live_data(provider_snapshots, asset)

    # ── Private query helpers ─────────────────────────────────────────


# ---------------------------------------------------------------------------
# Pure formatting helpers — no I/O, no state
# ---------------------------------------------------------------------------


def _format_rag_documents(documents: list[Any]) -> str:
    """Format RAG documents into a structured section."""
    lines = ["HISTORICAL CONTEXT:"]
    for i, doc in enumerate(documents, 1):
        text = getattr(doc, "text_content", str(doc))
        lines.append(f"  [{i}] {text}")
    return "\n".join(lines)


def _format_live_data(
    provider_snapshots: dict[str, dict[str, Any]],
    asset: str,
) -> str:
    """Format live provider data with defensive fallbacks.

    Every field uses ``.get()`` with ``"N/A (provider degraded)"``
    fallback. Empty snapshots return a placeholder message.
    """
    if not provider_snapshots:
        return "LIVE MARKET DATA: No provider data available."

    lines = [f"LIVE MARKET DATA ({asset}):"]
    for provider_name, data in provider_snapshots.items():
        if not data:
            lines.append(
                f"  {provider_name.upper()}: N/A (provider degraded)",
            )
            continue
        fields = _extract_provider_fields(provider_name, data)
        lines.append(f"  {provider_name.upper()}: {fields}")
    return "\n".join(lines)


def _extract_provider_fields(
    provider_name: str,
    data: dict[str, Any],
) -> str:
    """Extract and format fields from a single provider snapshot."""
    fallback = "N/A (provider degraded)"
    parts: list[str] = []
    for key, value in data.items():
        display_val = value if value is not None else fallback
        parts.append(f"{key}={display_val}")
    return "; ".join(parts) if parts else fallback


def _format_agent_verdicts(agent_results: list[AgentResult]) -> str:
    """Format agent verdicts grouped by category.

    Empty results return a placeholder. Per-category display shows
    score, max, and up to 3 explanations.
    """
    if not agent_results:
        return "AGENT VERDICTS: No agent results this cycle."

    lines = ["AGENT VERDICTS:"]
    category_groups = _group_by_category(agent_results)

    for category in AGENT_VERDICT_CATEGORY_ORDER:
        results = category_groups.get(category, [])
        _append_category_line(lines, category, results)

    total_score = sum(r.score for r in agent_results)
    lines.append(f"TOTAL RAW: {total_score:.1f}/220")
    return "\n".join(lines)


def _group_by_category(
    agent_results: list[AgentResult],
) -> dict[str, list[AgentResult]]:
    """Group agent results by inferred category."""
    groups: dict[str, list[AgentResult]] = {}
    for result in agent_results:
        cat = _infer_category(result.agent_name)
        groups.setdefault(cat, []).append(result)
    return groups


def _infer_category(agent_name: str) -> str:
    """Infer verdict bucket from agent name (substring heuristics)."""
    name_lower = agent_name.lower()
    if "derivatives" in name_lower or "funding" in name_lower:
        return "derivatives"
    if "whale" in name_lower or "onchain" in name_lower:
        return "onchain"
    if "sentiment" in name_lower:
        return "sentiment"
    if "technical" in name_lower:
        return "technical"
    if "regime" in name_lower or "macro" in name_lower or "news_macro" in name_lower:
        return "market_context"
    for category in AGENT_VERDICT_CATEGORY_ORDER:
        if category in name_lower:
            return category
    return "technical"


def _append_category_line(
    lines: list[str],
    category: str,
    results: list[AgentResult],
) -> None:
    """Append a single category line to the output."""
    default_technical_cap = CATEGORY_MAX_POINTS["technical"]
    max_score = AGENT_VERDICT_CATEGORY_MAX_POINTS.get(category, default_technical_cap)
    if not results:
        lines.append(
            f"  {category.upper()} (0.0/{max_score}): No data.",
        )
        return
    total = sum(r.score for r in results)
    explanations = [
        r.explanation for r in results if r.explanation
    ][:3]
    explanation_text = "; ".join(explanations) if explanations else "No explanations."
    lines.append(
        f"  {category.upper()} ({total:.1f}/{max_score}):"
        f" {explanation_text}",
    )


def _build_user_message(
    query: str,
    rag_section: str,
    live_section: str,
    agents_section: str,
) -> str:
    """Build the user message from ordered sections.

    Order: agents_section, live_section (if non-empty),
    rag_section (if non-empty), then QUERY.
    Returns a NEW string — never mutates inputs.
    """
    parts: list[str] = [agents_section]
    if live_section:
        parts.append(live_section)
    if rag_section:
        parts.append(rag_section)
    parts.append(f"QUERY: {query}")
    return "\n\n".join(parts)


def _estimate_tokens(text: str) -> int:
    """Rough token estimate for budget gating only."""
    return int(len(text.split()) * _TOKENS_PER_WORD)


def _compute_truncated_rag(
    query: str,
    rag_section: str,
    live_section: str,
    agents_section: str,
    max_tokens: int,
    system_section: str,
) -> str:
    """Compute truncated RAG section to fit within token budget.

    Truncation priority (never truncate items higher):
        1. system_section — never truncated
        2. agents_section — never truncated
        3. live_section — never truncated
        4. query — never truncated
        5. rag_section — trimmed first (remove from bottom)

    Returns:
        The truncated RAG section string.
    """
    system_tokens = _estimate_tokens(system_section)
    budget = max_tokens - system_tokens

    agents_tokens = _estimate_tokens(agents_section)
    live_tokens = _estimate_tokens(live_section) if live_section else 0
    query_tokens = _estimate_tokens(f"QUERY: {query}")

    reserved = agents_tokens + live_tokens + query_tokens
    rag_budget = max(0, budget - reserved)

    return _trim_rag_section(rag_section, rag_budget)


def _trim_rag_section(rag_section: str, token_budget: int) -> str:
    """Remove documents from the bottom of RAG section to fit budget.

    Returns empty string if budget is zero or negative.
    """
    if token_budget <= 0 or not rag_section:
        return ""
    lines = rag_section.split("\n")
    result_lines: list[str] = []
    current_tokens = 0
    for line in lines:
        line_tokens = _estimate_tokens(line)
        if current_tokens + line_tokens > token_budget:
            break
        result_lines.append(line)
        current_tokens += line_tokens
    return "\n".join(result_lines)
