"""Build the 33-asset dashboard ladder from Redis rotation — mirrors frontend logic."""

from __future__ import annotations

from backend.config.asset_universe import POLARIS_DASHBOARD_FALLBACK_BASES_ORDER as _FALLBACK_ACTIVE_33_BASES_ORDER

_TARGET_CARD_COUNT = 33

# Maximum unique pairs monitored on the dashboard horizon (pins first, then rotation ladder).
# Keep in sync with ``frontend/src/lib/dashboard-universe.ts`` ``MAX_DASHBOARD_MONITORED_ASSETS``.
MAX_DASHBOARD_MONITORED_ASSETS: int = 8


def derive_pair_from_rotation_entry(raw: str) -> str:
    """Normalize rotation Redis entries to ``BASE/USDT``."""
    trimmed = raw.strip().upper()
    if "/" in trimmed:
        base, quote = trimmed.split("/", 1)
        return f"{base}/{quote}"
    if trimmed.endswith("-PERP"):
        base = trimmed[: -len("-PERP")]
        return f"{base}/USDT"
    if trimmed.endswith("USDT"):
        base = trimmed[:-len("USDT")]
        return f"{base}/USDT"
    return f"{trimmed}/USDT"


def derive_base_asset_from_pair(pair_or_symbol: str) -> str:
    """Return uppercase base token."""
    trimmed = pair_or_symbol.strip().upper()
    if "/" in trimmed:
        return trimmed.split("/", 1)[0]
    if trimmed.endswith("USDT"):
        return trimmed[: -len("USDT")]
    if trimmed.endswith("-PERP"):
        return trimmed[: -len("-PERP")]
    return trimmed


def calculate_dashboard_pairs(active_33_from_redis: list[str] | None) -> list[str]:
    """Return exactly 33 ``BASE/USDT`` pairs — same ordering contract as the dashboard."""
    out: list[str] = []
    seen: set[str] = set()

    for entry in active_33_from_redis or []:
        normalized = derive_pair_from_rotation_entry(entry)
        key = normalized.upper()
        if key not in seen:
            seen.add(key)
            out.append(normalized)
        if len(out) >= _TARGET_CARD_COUNT:
            return out[:_TARGET_CARD_COUNT]

    for base in _FALLBACK_ACTIVE_33_BASES_ORDER:
        pair = f"{base}/USDT"
        key = pair.upper()
        if key not in seen:
            seen.add(key)
            out.append(pair)
        if len(out) >= _TARGET_CARD_COUNT:
            break

    return out[:_TARGET_CARD_COUNT]


def merge_dashboard_monitored_pairs(base_pairs: list[str], pinned_pairs: list[str]) -> list[str]:
    """Merge rotation ladder pairs with operator pins, dedupe, cap at ``MAX_DASHBOARD_MONITORED_ASSETS``.

    Pins are honoured first; remaining slots fill from ``base_pairs`` in order. Mirrors the dashboard UI.
    """
    seen_pairs: set[str] = set()
    merged_pairs: list[str] = []

    def append_unique(raw_pair: str) -> None:
        if len(merged_pairs) >= MAX_DASHBOARD_MONITORED_ASSETS:
            return
        canonical_pair = derive_pair_from_rotation_entry(raw_pair)
        dedupe_key = canonical_pair.upper()
        if dedupe_key in seen_pairs:
            return
        seen_pairs.add(dedupe_key)
        merged_pairs.append(canonical_pair)

    for raw_pin in pinned_pairs:
        append_unique(raw_pin)
    for raw_base in base_pairs:
        if len(merged_pairs) >= MAX_DASHBOARD_MONITORED_ASSETS:
            break
        append_unique(raw_base)

    return merged_pairs
