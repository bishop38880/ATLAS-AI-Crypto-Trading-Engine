"""Pure helpers for the decision journal API — no I/O."""

from __future__ import annotations

from typing import Any, Mapping


def parse_jsonb_list(value: Any) -> list[dict[str, Any]]:
    """Normalise asyncpg / JSONB agent_verdicts into a list of dicts."""
    if value is None:
        return []
    if isinstance(value, list):
        return [v for v in value if isinstance(v, dict)]
    return []


def resolve_primary_agent(
    agent_verdicts: list[dict[str, Any]],
    metadata: Mapping[str, Any] | None,
) -> str | None:
    """Pick vetoing agent first, else highest raw score contribution."""
    if agent_verdicts:
        veto = next((a for a in agent_verdicts if a.get("veto")), None)
        if isinstance(veto, dict) and veto.get("agent_name"):
            return str(veto["agent_name"])
        ranked = sorted(
            agent_verdicts,
            key=lambda a: int(a.get("score", 0) or 0),
            reverse=True,
        )
        if ranked and ranked[0].get("agent_name"):
            return str(ranked[0]["agent_name"])
    meta = metadata or {}
    primary = meta.get("primary_agent") or meta.get("primaryAgent")
    if isinstance(primary, str) and primary.strip():
        return primary.strip()
    return None


def outcome_horizons_from_metadata(
    metadata: Mapping[str, Any] | None,
    fallback_pnl: float | None,
) -> tuple[float | None, float | None, float | None]:
    """Read 1h / 4h / 24h outcome % from metadata; fill gaps with final PnL when absent."""
    meta = metadata or {}

    def pick(*keys: str) -> float | None:
        for key in keys:
            raw = meta.get(key)
            if raw is None:
                continue
            try:
                return float(raw)
            except (TypeError, ValueError):
                continue
        return None

    h1 = pick("outcome_pct_1h", "outcomePct1h", "pnl_pct_1h")
    h4 = pick("outcome_pct_4h", "outcomePct4h", "pnl_pct_4h")
    h24 = pick("outcome_pct_24h", "outcomePct24h", "pnl_pct_24h", "horizon_24h_pnl_pct")
    if h1 is None and fallback_pnl is not None:
        h1 = fallback_pnl
    if h4 is None and fallback_pnl is not None:
        h4 = fallback_pnl
    if h24 is None:
        h24 = fallback_pnl
    return h1, h4, h24


def matches_outcome_filter(
    outcome_label: str | None,
    pnl_pct: float | None,
    filt: str,
) -> bool:
    """Client-side outcome bucket (used when filtering joined rows)."""
    if filt in ("", "all"):
        return True
    if filt == "pending":
        return pnl_pct is None and outcome_label is None
    if filt == "win":
        return outcome_label == "WIN"
    if filt == "loss":
        return outcome_label == "LOSS"
    if filt == "scratch":
        return outcome_label == "SCRATCH"
    return True


def matches_agent_filter(primary: str | None, needle: str) -> bool:
    if not needle or not needle.strip():
        return True
    if primary is None:
        return False
    return needle.strip().lower() in primary.lower()


def build_score_breakdown_label(
    score: int,
    raw_score: int,
    confidence: float,
) -> str:
    return f"{score}/100 · raw {raw_score}/220 · conf {confidence:.2f}"

