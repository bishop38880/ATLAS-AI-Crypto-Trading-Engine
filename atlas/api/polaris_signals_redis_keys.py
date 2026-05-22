"""Redis key candidates for cached ``SignalOutput`` blobs.

Writers may persist under ``polaris:signals:{asset}`` or ``signal:latest:{asset}``,
using ``BTC``, ``BTCUSDT``, ``BTC/USDT``, etc. Dashboard deep-links by base symbol
(``/signals/BTC``). REST feed/detail must try the same alias families.

Render Network rebranded tickers: exchanges use ``RENDER*`` while rotation metadata
and older paths still say ``RNDR`` — both spellings must resolve to the same Redis keys.
"""

from __future__ import annotations

# Perp venues list RENDER; internal ladders / legacy rotation entries may still say RNDR.
_REBRAND_BASE_SYNONYMS: dict[str, str] = {
    "RNDR": "RENDER",
    "RENDER": "RNDR",
}


def _seed_tokens_for_query(asset_upper: str) -> list[str]:
    """Return primary plus exchange-base synonym seeds (e.g. RNDR ↔ RENDER)."""
    seeds: list[str] = [asset_upper]
    if asset_upper == "RNDR":
        seeds.append("RENDER")
    elif asset_upper == "RENDER":
        seeds.append("RNDR")
    elif asset_upper.endswith("USDT") and len(asset_upper) > 4:
        base = asset_upper[:-4]
        alt = _REBRAND_BASE_SYNONYMS.get(base)
        if alt is not None:
            seeds.append(f"{alt}USDT")
    elif "/" in asset_upper:
        base, _, quote = asset_upper.partition("/")
        alt = _REBRAND_BASE_SYNONYMS.get(base)
        if alt is not None and quote:
            seeds.append(f"{alt}/{quote}")
    return list(dict.fromkeys(seeds))


def _wire_aliases_for_normalized_token(normalized_asset: str) -> list[str]:
    """Single-seed expansion: base, slash pair, and concatenated USDT."""
    candidates: list[str] = [normalized_asset]

    if "/" in normalized_asset:
        base, _, quote = normalized_asset.partition("/")
        if quote == "USDT" and base:
            candidates.append(base)
            candidates.append(f"{base}USDT")
    elif normalized_asset.endswith("USDT") and len(normalized_asset) > 4:
        base = normalized_asset[:-4]
        candidates.append(base)
        candidates.append(f"{base}/USDT")
    else:
        candidates.append(f"{normalized_asset}/USDT")
        candidates.append(f"{normalized_asset}USDT")

    seen: set[str] = set()
    ordered_unique: list[str] = []
    for token in candidates:
        if not token or token in seen:
            continue
        seen.add(token)
        ordered_unique.append(token)

    return ordered_unique


def polaris_signal_wire_tokens(asset: str) -> list[str]:
    """Deduped wire tokens for Redis price keys, signal KV keys, and DB asset columns."""
    root = asset.strip().upper()
    seen: set[str] = set()
    ordered: list[str] = []
    for seed in _seed_tokens_for_query(root):
        for tok in _wire_aliases_for_normalized_token(seed):
            if tok and tok not in seen:
                seen.add(tok)
                ordered.append(tok)
    return ordered


def polaris_signal_redis_keys(asset: str) -> list[str]:
    """Return Redis keys to try for ``polaris:signals:*`` string cache."""
    return [f"polaris:signals:{candidate}" for candidate in polaris_signal_wire_tokens(asset)]


def signal_latest_redis_keys(asset: str) -> list[str]:
    """Return Redis keys for ``signal:latest:*`` (SignalStateManager KV path)."""
    return [f"signal:latest:{candidate}" for candidate in polaris_signal_wire_tokens(asset)]
