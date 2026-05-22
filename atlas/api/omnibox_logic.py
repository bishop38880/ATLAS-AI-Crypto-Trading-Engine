"""OmniBox routing heuristics — pure functions (unit-tested)."""

from __future__ import annotations

from atlas.shared.config import ModelStackConfig

_RAG_HINTS: tuple[str, ...] = (
    "historical",
    "similar",
    "signal",
    "pattern",
    "rag",
    "memory",
    "past",
    "outcome",
    "recorded",
    "lesson",
)
_MCP_HINTS: tuple[str, ...] = (
    "funding",
    "open interest",
    "oi ",
    "liquidation",
    "hydra",
    "live",
    "real-time",
    "realtime",
    "order book",
    "orderbook",
    "basis",
    "perp",
    "perpetual",
    "cvd",
    "long short",
)


def classify_omnibox_route(
    query: str,
    route_override: str | None,
) -> tuple[str, str]:
    """Return (route_key, short_rationale) for SSE classification."""
    if route_override in {"RAG_ONLY", "MCP_ONLY", "HYBRID", "DIRECT"}:
        return route_override, f"Route locked to {route_override} via operator override."

    q = query.lower()
    has_rag = any(h in q for h in _RAG_HINTS)
    has_mcp = any(h in q for h in _MCP_HINTS)
    if has_rag and not has_mcp:
        return "RAG_ONLY", "Query language skews toward historical / memory retrieval."
    if has_mcp and not has_rag:
        return "MCP_ONLY", "Query language skews toward live market / derivatives context."
    if has_rag and has_mcp:
        return "HYBRID", "Query blends historical memory and live-market themes."
    return "HYBRID", "Default hybrid routing for general operator questions."


def omnibox_context_flags(route: str) -> tuple[bool, bool]:
    """Return (use_rag, attach_live_snapshot) for the route."""
    if route == "RAG_ONLY":
        return True, False
    if route == "MCP_ONLY":
        return False, True
    if route == "HYBRID":
        return True, True
    return False, False


def omnibox_backend_is_deepseek(settings: ModelStackConfig) -> bool:
    """True when OmniBox should call hosted DeepSeek instead of LM Studio."""
    return settings.omnibox_chat_backend.strip().lower() == "deepseek"


def resolved_omnibox_lmstudio_model_id(cfg: ModelStackConfig) -> str:
    """Resolve LM Studio chat model id: explicit omnibox then router defaults."""
    direct = cfg.omnibox_chat_model.strip()
    if direct:
        return direct
    for candidate in (
        cfg.router_local_model.strip(),
        cfg.router_api_model.strip(),
    ):
        if candidate:
            return candidate
    return "mistral"
