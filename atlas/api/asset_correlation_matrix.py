"""Rolling Pearson correlations across dashboard assets (daily USDM closes).

Uses Binance USDⓈ-M ``klines`` (1d). Cached in Redis to avoid burst requests.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Any

import httpx
import msgspec
from loguru import logger
from redis.asyncio import Redis

from atlas.api.dashboard_rotation_pairs import derive_base_asset_from_pair

_BINANCE_FAPI_KLINES = "https://fapi.binance.com/fapi/v1/klines"

# Binance listing quirks — try alternates after primary ``{BASE}USDT`` fails.
_EXTRA_BINANCE_USDM_TRIES: dict[str, tuple[str, ...]] = {
    "RNDR": ("RENDERUSDT",),
    "MATIC": ("POLUSDT",),
}

_HIGH_CORR_ALERT = 0.92
_CLUSTER_EDGE = 0.85

_CACHE_KEY = "polaris:dashboard:asset_correlation:v1"
CACHE_TTL_SECONDS = 300

_FETCH_CONCURRENCY = 8
_KLINE_LIMIT = 48


class CorrelationMatrixCachePayload(msgspec.Struct):
    pairs: list[str]
    generated_at: str
    matrix_14: list[list[float | None]]
    matrix_30: list[list[float | None]]
    fetch_errors: dict[str, str]


def _pair_to_binance_candidates(pair: str) -> list[str]:
    base = derive_base_asset_from_pair(pair)
    primary = f"{base}USDT"
    extras = _EXTRA_BINANCE_USDM_TRIES.get(base.upper(), ())
    ordered: list[str] = [primary]
    for sym in extras:
        if sym not in ordered:
            ordered.append(sym)
    return ordered


def _log_returns(closes: list[float]) -> list[float]:
    if len(closes) < 2:
        return []
    out: list[float] = []
    for idx in range(1, len(closes)):
        prev = closes[idx - 1]
        cur = closes[idx]
        if prev <= 0.0 or cur <= 0.0:
            continue
        out.append(float(math.log(cur / prev)))
    return out


def _pearson(sample_x: list[float], sample_y: list[float]) -> float | None:
    """Pearson correlation; ``None`` if undefined."""
    n = min(len(sample_x), len(sample_y))
    if n < 2:
        return None
    x = sample_x[-n:]
    y = sample_y[-n:]
    mean_x = sum(x) / n
    mean_y = sum(y) / n
    cov = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n))
    var_x = sum((x[i] - mean_x) ** 2 for i in range(n))
    var_y = sum((y[i] - mean_y) ** 2 for i in range(n))
    if var_x <= 1e-18 or var_y <= 1e-18:
        return None
    return cov / (var_x ** 0.5 * var_y ** 0.5)


def build_correlation_matrix(
    returns_by_asset: list[list[float]],
    window: int,
) -> list[list[float | None]]:
    """Symmetric matrix of pairwise correlations over the last ``window`` returns."""
    n = len(returns_by_asset)
    matrix: list[list[float | None]] = [
        [None] * n for _ in range(n)
    ]
    for i in range(n):
        matrix[i][i] = 1.0
    for i in range(n):
        for j in range(i + 1, n):
            ri = returns_by_asset[i]
            rj = returns_by_asset[j]
            if len(ri) < window or len(rj) < window:
                rho = None
            else:
                rho = _pearson(ri[-window:], rj[-window:])
            matrix[i][j] = rho
            matrix[j][i] = rho
    return matrix


class _UnionFind:
    def __init__(self, n: int) -> None:
        self._parent = list(range(n))
        self._rank = [0] * n

    def find(self, x: int) -> int:
        if self._parent[x] != x:
            self._parent[x] = self.find(self._parent[x])
        return self._parent[x]

    def union(self, a: int, b: int) -> None:
        ra = self.find(a)
        rb = self.find(b)
        if ra == rb:
            return
        if self._rank[ra] < self._rank[rb]:
            ra, rb = rb, ra
        self._parent[rb] = ra
        if self._rank[ra] == self._rank[rb]:
            self._rank[ra] += 1


def find_high_correlation_clusters(
    matrix: list[list[float | None]],
    bases: list[str],
    edge_threshold: float = _CLUSTER_EDGE,
) -> list[dict[str, Any]]:
    """Connected components on pairs with |ρ| ≥ threshold (absolute co-movement)."""
    n = len(bases)
    uf = _UnionFind(n)
    for i in range(n):
        for j in range(i + 1, n):
            rho = matrix[i][j]
            if rho is None:
                continue
            if abs(rho) >= edge_threshold:
                uf.union(i, j)
    groups: dict[int, list[int]] = {}
    for idx in range(n):
        root = uf.find(idx)
        groups.setdefault(root, []).append(idx)
    clusters: list[dict[str, Any]] = []
    for members in groups.values():
        if len(members) < 2:
            continue
        max_rho = 0.0
        for a_idx in range(len(members)):
            for b_idx in range(a_idx + 1, len(members)):
                i = members[a_idx]
                j = members[b_idx]
                rho = matrix[i][j]
                if rho is not None:
                    max_rho = max(max_rho, abs(rho))
        clusters.append(
            {
                "bases": sorted(bases[i] for i in members),
                "size": len(members),
                "max_abs_correlation": max_rho,
            },
        )
    clusters.sort(key=lambda c: (-int(c["size"]), -float(c["max_abs_correlation"])))
    return clusters


def collect_extreme_pairs(
    matrix: list[list[float | None]],
    bases: list[str],
    threshold: float = _HIGH_CORR_ALERT,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    n = len(bases)
    for i in range(n):
        for j in range(i + 1, n):
            rho = matrix[i][j]
            if rho is None:
                continue
            if rho >= threshold:
                out.append(
                    {
                        "base_a": bases[i],
                        "base_b": bases[j],
                        "correlation": rho,
                    },
                )
    out.sort(key=lambda row: -float(row["correlation"]))
    return out


def analyse_position_concentration(
    matrix: list[list[float | None]],
    bases: list[str],
    position_bases: list[str],
) -> dict[str, Any]:
    """Flag highly correlated simultaneous holdings."""
    idx_map = {b.upper(): k for k, b in enumerate(bases)}
    positions = sorted({p.strip().upper() for p in position_bases if p.strip()})
    resolved = [idx_map[p] for p in positions if p in idx_map]
    label_positions = {bases[i] for i in resolved}
    if len(resolved) < 2:
        return {
            "level": "ok",
            "message": "Need two or more active ladder positions on monitored assets to evaluate overlap.",
            "average_pairwise_correlation": None,
            "monitored_positions": sorted(label_positions),
        }
    rhos: list[float] = []
    for a_idx in range(len(resolved)):
        for b_idx in range(a_idx + 1, len(resolved)):
            i = resolved[a_idx]
            j = resolved[b_idx]
            rho = matrix[i][j]
            if rho is not None:
                rhos.append(rho)
    if not rhos:
        return {
            "level": "warn",
            "message": "Could not compute overlap — missing return history for some open symbols.",
            "average_pairwise_correlation": None,
            "monitored_positions": sorted(label_positions),
        }
    avg_rho = sum(rhos) / len(rhos)
    clusters = find_high_correlation_clusters(matrix, bases, _CLUSTER_EDGE)
    trapped_in_single_bucket = False
    pos_set = set(label_positions)
    for cl in clusters:
        memb = set(str(x) for x in cl["bases"])
        if len(pos_set) >= 2 and pos_set.issubset(memb) and len(memb) >= 3:
            trapped_in_single_bucket = True
            break

    level = "ok"
    message = (
        f"Average pairwise ρ among open monitors is {avg_rho:.2f} "
        f"({len(resolved)} positions)."
    )
    if avg_rho >= _HIGH_CORR_ALERT or trapped_in_single_bucket:
        level = "critical"
        message = (
            "Holdings map into the same high-correlation bucket — effective diversification is low. "
            f"Average pairwise ρ ≈ {avg_rho:.2f}. Consider trimming overlapping beta."
        )
    elif avg_rho >= _CLUSTER_EDGE:
        level = "warn"
        message = (
            "Elevated co-movement across open monitors — overlap risk is building. "
            f"Average pairwise ρ ≈ {avg_rho:.2f}."
        )

    return {
        "level": level,
        "message": message,
        "average_pairwise_correlation": avg_rho,
        "monitored_positions": sorted(label_positions),
    }


async def _fetch_usdm_daily_closes(
    client: httpx.AsyncClient,
    pair: str,
    semaphore: asyncio.Semaphore,
) -> tuple[str, list[float] | None, str | None]:
    candidates = _pair_to_binance_candidates(pair)
    async with semaphore:
        for sym in candidates:
            try:
                response = await asyncio.wait_for(
                    client.get(
                        _BINANCE_FAPI_KLINES,
                        params={
                            "symbol": sym,
                            "interval": "1d",
                            "limit": _KLINE_LIMIT,
                        },
                    ),
                    timeout=25.0,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "correlation_klines_http_error | pair={} | symbol={} | err={}",
                    pair,
                    sym,
                    str(exc),
                )
                continue
            if response.status_code != 200:
                logger.debug(
                    "correlation_klines_non_ok | pair={} | symbol={} | status={}",
                    pair,
                    sym,
                    response.status_code,
                )
                continue
            try:
                payload = msgspec.json.decode(response.content)
            except Exception as exc:
                logger.warning(
                    "correlation_klines_decode_failed | pair={} | err={}",
                    pair,
                    str(exc),
                )
                continue
            if not isinstance(payload, list):
                continue
            closes: list[float] = []
            for row in payload:
                if not isinstance(row, list) or len(row) < 5:
                    continue
                close_field = row[4]
                closes.append(float(close_field))
            if len(closes) < 16:
                continue
            return pair, closes, None
    return pair, None, "no_usdm_history"


@dataclass(frozen=True)
class CorrelationComputeResult:
    pairs: list[str]
    bases: list[str]
    matrix_14: list[list[float | None]]
    matrix_30: list[list[float | None]]
    clusters_30: list[dict[str, Any]]
    extreme_pairs_30: list[dict[str, Any]]
    fetch_errors: dict[str, str]
    generated_at: str


async def compute_asset_correlation_snapshot(
    client: httpx.AsyncClient,
    pairs: list[str],
) -> CorrelationComputeResult:
    semaphore = asyncio.Semaphore(_FETCH_CONCURRENCY)
    tasks = [_fetch_usdm_daily_closes(client, pair, semaphore) for pair in pairs]
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    fetch_errors: dict[str, str] = {}
    closes_map: dict[str, list[float]] = {}
    for item in raw_results:
        if isinstance(item, BaseException):
            logger.exception("correlation_fetch_task_failed | err={}", str(item))
            continue
        pair, closes, err = item
        if closes is None:
            fetch_errors[derive_base_asset_from_pair(pair)] = err or "unknown"
            continue
        closes_map[pair] = closes

    ordered_pairs = list(pairs)
    returns_by_asset: list[list[float]] = []
    for pair in ordered_pairs:
        c = closes_map.get(pair)
        if c is None:
            returns_by_asset.append([])
        else:
            returns_by_asset.append(_log_returns(c))

    matrix_14 = build_correlation_matrix(returns_by_asset, 14)
    matrix_30 = build_correlation_matrix(returns_by_asset, 30)
    bases = [derive_base_asset_from_pair(p) for p in ordered_pairs]
    clusters_30 = find_high_correlation_clusters(matrix_30, bases)
    extreme_pairs_30 = collect_extreme_pairs(matrix_30, bases)
    stamp = datetime.now(timezone.utc).isoformat()

    return CorrelationComputeResult(
        pairs=ordered_pairs,
        bases=bases,
        matrix_14=matrix_14,
        matrix_30=matrix_30,
        clusters_30=clusters_30,
        extreme_pairs_30=extreme_pairs_30,
        fetch_errors=fetch_errors,
        generated_at=stamp,
    )


async def load_or_compute_correlation_snapshot(
    redis: Redis,
    client: httpx.AsyncClient,
    pairs: list[str],
) -> CorrelationComputeResult:
    """Return cached snapshot when universe matches; otherwise fetch & store."""
    fingerprint = "|".join(pairs)
    try:
        raw_cache = await redis.get(_CACHE_KEY)
        if raw_cache:
            decoded = msgspec.json.decode(raw_cache, type=CorrelationMatrixCachePayload)
            if "|".join(decoded.pairs) == fingerprint:
                bases = [derive_base_asset_from_pair(p) for p in decoded.pairs]
                return CorrelationComputeResult(
                    pairs=list(decoded.pairs),
                    bases=bases,
                    matrix_14=decoded.matrix_14,
                    matrix_30=decoded.matrix_30,
                    clusters_30=find_high_correlation_clusters(decoded.matrix_30, bases),
                    extreme_pairs_30=collect_extreme_pairs(decoded.matrix_30, bases),
                    fetch_errors=dict(decoded.fetch_errors),
                    generated_at=decoded.generated_at,
                )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("correlation_cache_read_failed | err={}", str(exc))

    snapshot = await compute_asset_correlation_snapshot(client, pairs)
    cache_payload = CorrelationMatrixCachePayload(
        pairs=snapshot.pairs,
        generated_at=snapshot.generated_at,
        matrix_14=snapshot.matrix_14,
        matrix_30=snapshot.matrix_30,
        fetch_errors=snapshot.fetch_errors,
    )
    try:
        await redis.setex(
            _CACHE_KEY,
            CACHE_TTL_SECONDS,
            msgspec.json.encode(cache_payload),
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("correlation_cache_write_failed | err={}", str(exc))
    return snapshot
