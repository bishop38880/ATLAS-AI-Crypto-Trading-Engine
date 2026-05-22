"""Pure helpers for per-agent vote history on `/api/agents/{name}/history`."""

from __future__ import annotations

from typing import Any, Iterable


def normalize_agent_match_key(raw: str) -> str:
    """Alphanumeric lower token — mirrors frontend {@link normalize_agent_match_token}."""
    return "".join(ch.lower() for ch in raw if ch.isalnum())


# Frontend `/ws/agents` live names (normalized) → possible ``AgentVerdict.agent_name`` keys.
_WS_AGENT_HISTORY_ALIAS_GROUPS: dict[str, frozenset[str]] = {
    "derivativesagent": frozenset({"derivativesagent", "derivatives", "derivatives_agent"}),
    "technicalagent": frozenset({"technicalagent", "technical"}),
    "whalewatcheragent": frozenset(
        {"whalewatcheragent", "whale", "onchain", "onchainagent", "whaleagent"},
    ),
    "socialagent": frozenset({"socialagent", "sentiment", "social"}),
    "macroagent": frozenset({"macroagent", "regime", "macro", "newsmacroagent", "news_macro_agent"}),
    "liquidationagent": frozenset({"liquidationagent", "liquidation"}),
    "whaleoverlayagent": frozenset({"whaleoverlayagent", "whaleoverlay"}),
    "fundingratemonitoragent": frozenset(
        {"fundingratemonitoragent", "fundingratemonitor", "funding_rate_monitor", "funding"},
    ),
    "correlationmonitor": frozenset({"correlationmonitor", "correlationagent", "correlation"}),
    "correlationagent": frozenset({"correlationmonitor", "correlationagent", "correlation"}),
    "risk": frozenset({"risk", "riskagent"}),
    "newsmacroagent": frozenset({"newsmacroagent", "macroresearchagent", "news_macro_agent"}),
}


def resolve_agent_alias_keys(encoded_ws_agent_name: str) -> frozenset[str]:
    """Expand websocket/UI agent label into verdict ``agent_name`` tokens."""
    key = normalize_agent_match_key(encoded_ws_agent_name)
    if key in _WS_AGENT_HISTORY_ALIAS_GROUPS:
        return _WS_AGENT_HISTORY_ALIAS_GROUPS[key]
    return frozenset({key})


def find_agent_verdict_row(
    verdicts: Iterable[dict[str, Any]],
    encoded_ws_agent_name: str,
) -> dict[str, Any] | None:
    """Pick the verdict row for this dashboard agent (first alias hit preserves stable UX)."""
    aliases = resolve_agent_alias_keys(encoded_ws_agent_name)
    normalized_aliases = {normalize_agent_match_key(a) for a in aliases}
    for verdict in verdicts:
        if not isinstance(verdict, dict):
            continue
        raw_name = verdict.get("agent_name") or verdict.get("agentName") or ""
        vn = normalize_agent_match_key(str(raw_name))
        if vn in normalized_aliases:
            return verdict
    return None


def trade_direction_from_decision(decision: str) -> str | None:
    """Map stored signal decision string to bullish/bearish trade thesis (None if flat)."""
    d = decision.strip().lower()
    if d in ("long", "strong buy", "buy"):
        return "bullish"
    if d in ("short", "strong sell", "sell"):
        return "bearish"
    return None


def normalize_agent_direction(direction: str) -> str:
    return direction.strip().lower()


def infer_vote_correctness(
    *,
    trade_dir: str | None,
    agent_dir: str | None,
    outcome_label: str | None,
) -> bool | None:
    """Whether the agent's directional vote matched realised PnL vs the traded side.

    WIN on a long: bullish votes correct, bearish incorrect; LOSS inverts.
    Neutral agent votes or non-directional trades yield ``None``.
    SCRATCH / pending → ``None``.
    """
    if trade_dir is None or agent_dir is None:
        return None
    ad = normalize_agent_direction(agent_dir)
    if ad == "neutral":
        return None
    if outcome_label not in ("WIN", "LOSS"):
        return None
    aligned = (trade_dir == "bullish" and ad == "bullish") or (
        trade_dir == "bearish" and ad == "bearish"
    )
    contradicted = (trade_dir == "bullish" and ad == "bearish") or (
        trade_dir == "bearish" and ad == "bullish"
    )
    if outcome_label == "WIN":
        if aligned:
            return True
        if contradicted:
            return False
        return None
    if contradicted:
        return True
    if aligned:
        return False
    return None
