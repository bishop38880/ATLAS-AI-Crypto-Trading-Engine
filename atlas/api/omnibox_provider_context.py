"""Assemble Redis-backed provider caches into one OmniBox LLM context block.

OmniBox does not call provider HTTP clients directly; it reads whatever the
running stack has already persisted to Redis (Coinalyze, Pyth, FRED, OKX MCP,
DeFi Llama, Alternative.me, latest signal KV, provider health, etc.) so the
chat model is grounded in the same feeds as the dashboard.

Tier-2 MCP providers (Dune, Nansen) do not expose a stable cross-asset Redis
mirror in-tree; their fields usually appear inside the cached POLARIS signal /
agent breakdown once a scoring cycle has run.
"""

from __future__ import annotations

from typing import Any

import msgspec
from loguru import logger
from redis.asyncio import Redis

from atlas.api._channel_reads import (
    COINGECKO_SIMPLE_PRICE_ID_BY_BASE,
    _unique_base_symbols_from_tokens,
    read_prices,
)
from atlas.api.polaris_signals_redis_keys import (
    polaris_signal_redis_keys,
    polaris_signal_wire_tokens,
    signal_latest_redis_keys,
)
from atlas.api.routes.providers import PROVIDER_REGISTRY, REDIS_KEY_MAP
from atlas.api.schemas import PricePayload
from atlas.core.provider_health import ProviderHealthState
from atlas.providers.alternative_me.models import AlternativeMeSnapshot
from atlas.providers.coinalyze.provider import (
    FundingRateSnapshot,
    LiquidationHistory,
    LongShortRatioHistory,
    OpenInterestSnapshot,
)
from atlas.providers.coingecko.adapter import PriceReferenceData
from atlas.providers.defillama.cache import (
    read_protocol_tvls,
    read_stablecoin_supply,
    read_yield_pools,
)
from atlas.providers.defillama.models import TVLSnapshot, YieldPool
from atlas.providers.fred.models import MacroSnapshot
from atlas.providers.okx_mcp import cache as okx_mcp_cache
from atlas.shared.config import PolarisSettings

_MAX_CONTEXT_CHARS = 16_000


def _lines_from_signal_dict(sig: dict[str, Any]) -> list[str]:
    """Turn a cached ``SignalOutput``-shaped dict into short LLM lines."""
    lines: list[str] = []
    asset_raw = sig.get("asset")
    if isinstance(asset_raw, str) and asset_raw.strip():
        lines.append("asset={}".format(asset_raw.strip()))
    decision = sig.get("decision")
    if isinstance(decision, str) and decision.strip():
        lines.append("decision={}".format(decision.strip()))
    score = sig.get("score")
    if score is not None:
        lines.append("score={}".format(score))
    ts = sig.get("timestamp")
    if isinstance(ts, str) and ts.strip():
        lines.append("timestamp={}".format(ts.strip()))
    rs = sig.get("reasoning_summary")
    if rs is None:
        rs = sig.get("reasoningSummary")
    if isinstance(rs, str) and rs.strip():
        lines.append("reasoning_summary={}".format(rs.strip()[:400]))
    return lines


async def _section_global_latest_signal(redis: Redis) -> list[str]:
    raw = await redis.get("polaris:latest_signal")
    if not raw:
        return []
    try:
        decoded = msgspec.json.decode(raw)
    except Exception:
        return []
    if not isinstance(decoded, dict):
        return []
    inner = _lines_from_signal_dict(decoded)
    if not inner:
        return []
    return [
        "### Latest POLARIS signal (global `polaris:latest_signal`)\n" + "\n".join(inner[:8]),
    ]


async def _section_signal(redis: Redis, asset_query: str) -> list[str]:
    keys = [
        *polaris_signal_redis_keys(asset_query),
        *signal_latest_redis_keys(asset_query),
    ]
    if not keys:
        return []
    blobs = await redis.mget(keys)
    for raw in blobs:
        if not raw:
            continue
        try:
            decoded = msgspec.json.decode(raw)
        except Exception:
            continue
        if isinstance(decoded, dict):
            inner = _lines_from_signal_dict(decoded)
            if inner:
                return [
                    "### Asset-scoped POLARIS signal (Redis KV)\n" + "\n".join(inner),
                ]
    return []


async def _section_pyth_oracle(redis: Redis, bases: list[str]) -> list[str]:
    lines: list[str] = []
    seen: set[str] = set()
    for base in bases:
        if base in seen:
            continue
        seen.add(base)
        raw = await redis.get("atlas:price:{}".format(base))
        if not raw:
            continue
        try:
            decoded: Any = msgspec.json.decode(raw)
        except Exception as exc:
            logger.warning("omnibox_pyth_oracle_decode | base={} | err={}", base, str(exc))
            continue
        if not isinstance(decoded, dict):
            continue
        price = decoded.get("price", "")
        conf = decoded.get("conf", "")
        pub = decoded.get("publish_time", "")
        lines.append(
            "Pyth Hermes | {} price={} conf={} publish_time={}".format(base, price, conf, pub),
        )
    if not lines:
        return []
    return ["### Pyth Hermes (oracle cache)\n" + "\n".join(lines)]


async def _section_coingecko_reference(redis: Redis, bases: list[str]) -> list[str]:
    lines: list[str] = []
    for base in bases:
        gecko_id = COINGECKO_SIMPLE_PRICE_ID_BY_BASE.get(base)
        if not gecko_id:
            continue
        raw = await redis.get("provider:coingecko:price:{}".format(gecko_id))
        if not raw:
            continue
        try:
            ref = PriceReferenceData.model_validate(msgspec.json.decode(raw))
        except Exception as exc:
            logger.warning(
                "omnibox_coingecko_ref_decode | base={} | err={}",
                base,
                str(exc),
            )
            continue
        lines.append(
            "CoinGecko ref | {} id={} price_usd={} status={} updated={}".format(
                base,
                gecko_id,
                ref.price_usd,
                ref.status,
                ref.last_updated_utc,
            ),
        )
    if not lines:
        return []
    return ["### CoinGecko (validation cross-price cache)\n" + "\n".join(lines)]


async def _section_coinalyze(redis: Redis, bases: list[str]) -> list[str]:
    for base in bases:
        out: list[str] = []
        raw_f = await redis.get("coinalyze:{}:funding".format(base))
        if raw_f:
            try:
                data = msgspec.json.decode(raw_f)
                if isinstance(data, dict):
                    fund = FundingRateSnapshot.model_validate(data)
                    out.append(
                        "Coinalyze funding | {} annualised_rate_pct={} exchanges={} status={}".format(
                            fund.asset,
                            fund.rate_pct,
                            fund.exchange_count,
                            fund.status,
                        ),
                    )
            except Exception as exc:
                logger.warning(
                    "omnibox_coinalyze_funding_decode | base={} | err={}",
                    base,
                    str(exc),
                )

        raw_oi = await redis.get("coinalyze:{}:oi".format(base))
        if raw_oi:
            try:
                data_oi = msgspec.json.decode(raw_oi)
                if isinstance(data_oi, dict):
                    oi = OpenInterestSnapshot.model_validate(data_oi)
                    out.append(
                        "Coinalyze OI | {} oi_usd={} exchanges={} status={}".format(
                            oi.asset,
                            oi.oi_usd,
                            oi.exchange_count,
                            oi.status,
                        ),
                    )
            except Exception as exc:
                logger.warning(
                    "omnibox_coinalyze_oi_decode | base={} | err={}",
                    base,
                    str(exc),
                )

        raw_liq = await redis.get("coinalyze:{}:liquidations:30m".format(base))
        if raw_liq:
            try:
                liq = LiquidationHistory.model_validate(msgspec.json.decode(raw_liq))
                out.append(
                    "Coinalyze liq_30m | {} long_liq_usd={} short_liq_usd={} status={}".format(
                        liq.asset,
                        liq.total_long_liq_usd,
                        liq.total_short_liq_usd,
                        liq.status,
                    ),
                )
            except Exception as exc:
                logger.warning(
                    "omnibox_coinalyze_liq_decode | base={} | err={}",
                    base,
                    str(exc),
                )

        raw_ls = await redis.get("coinalyze:{}:long_short:30m".format(base))
        if raw_ls:
            try:
                ls = LongShortRatioHistory.model_validate(msgspec.json.decode(raw_ls))
                out.append(
                    "Coinalyze long_short_30m | {} ratio={} long_pct={} status={}".format(
                        ls.asset,
                        ls.current_ratio,
                        ls.current_long_pct,
                        ls.status,
                    ),
                )
            except Exception as exc:
                logger.warning(
                    "omnibox_coinalyze_ls_decode | base={} | err={}",
                    base,
                    str(exc),
                )

        if out:
            return out
    return []


async def _section_okx_mcp(redis: Redis, bases: list[str]) -> list[str]:
    for base in bases:
        for cand in ("{}USDT".format(base), base):
            fr = await okx_mcp_cache.read_funding_rate(redis, cand)
            if fr is None:
                continue
            inst = cand
            lines: list[str] = [
                "OKX MCP funding | inst={} rate={} fetched_at_ms={}".format(
                    fr.inst_id,
                    fr.funding_rate,
                    fr.fetched_at_ms,
                ),
            ]
            oi = await okx_mcp_cache.read_open_interest(redis, inst)
            if oi is not None:
                lines.append(
                    "OKX MCP open_interest | inst={} oi={} oi_ccy={} ts={}".format(
                        oi.inst_id,
                        oi.oi,
                        oi.oi_ccy,
                        oi.ts,
                    ),
                )
            ls = await okx_mcp_cache.read_long_short_ratio(redis, inst)
            if ls is not None:
                lines.append(
                    "OKX MCP long_short | inst={} ratio={} long={} short={} ts={}".format(
                        ls.inst_id,
                        ls.long_short_ratio,
                        ls.long_ratio,
                        ls.short_ratio,
                        ls.ts,
                    ),
                )
            liq = await okx_mcp_cache.read_liquidations(redis, inst)
            if liq is not None:
                lines.append(
                    "OKX MCP liquidations | inst={} order_count={} fetched_at={}".format(
                        liq.inst_id,
                        len(liq.orders),
                        liq.fetched_at,
                    ),
                )
            return lines
    return []


async def _section_fred(redis: Redis) -> list[str]:
    raw = await redis.get("atlas:provider:fred:macro_snapshot")
    if not raw:
        return []
    try:
        snap = MacroSnapshot.model_validate(msgspec.json.decode(raw))
    except Exception as exc:
        logger.warning("omnibox_fred_decode | err={}", str(exc))
        return []
    return [
        "FRED macro | regime={} fed_funds={} cpi_yoy={} yc_spread={} stablecoin_supply_usd={} stale={}".format(
            snap.macro_regime,
            snap.fed_funds_rate,
            snap.cpi_yoy,
            snap.yield_curve.spread,
            snap.stablecoin_total_supply_usd,
            snap.stale,
        ),
    ]


async def _section_fear_greed(redis: Redis) -> list[str]:
    raw = await redis.get("atlas:provider:alternative_me:snapshot")
    if not raw:
        return []
    try:
        snap = AlternativeMeSnapshot.model_validate(msgspec.json.decode(raw))
    except Exception as exc:
        logger.warning("omnibox_altme_decode | err={}", str(exc))
        return []
    if snap.data is None:
        return ["Alternative.me | status={} stale={}".format(snap.status, snap.stale)]
    return [
        "Alternative.me Fear&Greed | value={} label={} status={}".format(
            snap.data.value,
            snap.data.value_classification,
            snap.status,
        ),
    ]


def _fmt_protocol_line(row: TVLSnapshot) -> str:
    return "protocol_tvl | name={} chain={} usd={} chg_1d_pct={} chg_7d_pct={}".format(
        row.protocol,
        row.chain,
        row.tvl_usd,
        row.tvl_change_1d_pct,
        row.tvl_change_7d_pct,
    )


def _fmt_pool_line(row: YieldPool) -> str:
    return "yield_pool | project={} chain={} symbol={} apy={} tvl_usd={}".format(
        row.project,
        row.chain,
        row.symbol,
        row.apy,
        row.tvl_usd,
    )


async def _section_defillama(redis: Redis) -> list[str]:
    blocks: list[str] = []
    sc = await read_stablecoin_supply(redis)
    if sc is not None:
        blocks.append(
            "stablecoins | total_mcap_usd={} usdt={} usdc={} usdt_dom_pct={}".format(
                sc.total_mcap_usd,
                sc.usdt_mcap_usd,
                sc.usdc_mcap_usd,
                sc.usdt_dominance_pct,
            ),
        )
    protocols = await read_protocol_tvls(redis)
    if protocols:
        ranked = sorted(protocols, key=lambda r: r.tvl_usd, reverse=True)[:6]
        blocks.extend(_fmt_protocol_line(p) for p in ranked)
    pools = await read_yield_pools(redis)
    if pools:
        ranked_p = sorted(pools, key=lambda r: r.tvl_usd, reverse=True)[:4]
        blocks.extend(_fmt_pool_line(p) for p in ranked_p)
    if not blocks:
        return []
    return ["### DeFi Llama (aggregate caches)\n" + "\n".join(blocks)]


async def _section_altfins(redis: Redis, bases: list[str]) -> list[str]:
    lines: list[str] = []
    for base in bases:
        raw_sig = await redis.get("provider:altfins:signals:{}".format(base))
        if raw_sig:
            try:
                decoded: Any = msgspec.json.decode(raw_sig)
            except Exception:
                decoded = None
            if isinstance(decoded, dict) and decoded:
                lines.append(
                    "Altfins signals | {} | {}".format(base, str(decoded)[:480]),
                )
                break
    for base in bases:
        raw = await redis.get("provider:altfins:summary:{}".format(base))
        if not raw:
            continue
        try:
            decoded2: Any = msgspec.json.decode(raw)
        except Exception:
            continue
        if isinstance(decoded2, dict) and decoded2:
            lines.append(
                "Altfins summary | {} | {}".format(base, str(decoded2)[:500]),
            )
            break
    if not lines:
        return []
    return ["### Altfins (cache)\n" + "\n".join(lines)]


async def _section_provider_health(redis: Redis) -> list[str]:
    parts: list[str] = []
    for entry in PROVIDER_REGISTRY:
        name = str(entry["name"])
        redis_key = REDIS_KEY_MAP.get(name, name.lower())
        raw = await redis.get("provider:{}:health_score".format(redis_key))
        if not raw:
            parts.append("{}=n/a".format(name))
            continue
        try:
            state = msgspec.json.decode(raw, type=ProviderHealthState)
        except Exception:
            parts.append("{}=?".format(name))
            continue
        parts.append("{}={:.2f}".format(name, state.health_score))
    return [
        "### Provider health (Redis rolling scores, 0–1)\n" + ", ".join(parts),
    ]


async def _section_operational_flags(redis: Redis) -> list[str]:
    lines: list[str] = []
    raw_cz = await redis.get("provider:coinalyze:status")
    if raw_cz:
        if isinstance(raw_cz, bytes):
            raw_cz = raw_cz.decode("utf-8", errors="replace")
        if str(raw_cz).upper() == "DEGRADED":
            lines.append("Coinalyze operational flag= DEGRADED")
    raw_okx = await redis.get("agent:okx_mcp:status")
    if raw_okx:
        lines.append("OKX MCP agent status blob present (see Redis `agent:okx_mcp:status`).")
    if not lines:
        return []
    return ["### Operational flags\n" + "\n".join(lines)]


def _section_prices_rows(rows: list[PricePayload]) -> list[str]:
    lines: list[str] = []
    for row in rows[:6]:
        d = row.model_dump(by_alias=True)
        sym = str(d.get("symbol", ""))
        price = d.get("price", "")
        chg = d.get("change_24h", "")
        ts = d.get("timestamp", "")
        lines.append(
            "{} price={} change_24h_pct={} as_of={}".format(sym, price, chg, ts),
        )
    return lines


async def _section_coinalyze_state(redis: Redis, bases: list[str]) -> list[str]:
    """Optional unified slot written by MCP / tooling (``coinalyze:state:{base}``)."""
    for base in bases:
        raw = await redis.get("coinalyze:state:{}".format(base))
        if not raw:
            continue
        try:
            decoded: Any = msgspec.json.decode(raw)
        except Exception:
            continue
        if isinstance(decoded, dict) and decoded:
            return ["Coinalyze MCP state | {} | {}".format(base, str(decoded)[:520])]
    return []


async def _chunks_asset_venue_and_derivatives(
    redis: Redis,
    settings: PolarisSettings,
    asset_focus: str,
    bases: list[str],
) -> list[str]:
    out: list[str] = []

    global_sig = await _section_global_latest_signal(redis)
    if global_sig:
        out.extend(global_sig)

    sig = await _section_signal(redis, asset_focus)
    if sig:
        out.extend(sig)

    try:
        price_rows = await read_prices(redis, settings, symbols_query=asset_focus)
    except Exception as exc:
        logger.warning("omnibox_read_prices_in_context | err={}", str(exc))
        price_rows = []
    if price_rows:
        out.append(
            "### Spot / venue price cache (Pyth → CoinGecko → Bitget order)\n"
            + "\n".join(_section_prices_rows(price_rows)),
        )

    pyth_lines = await _section_pyth_oracle(redis, bases)
    if pyth_lines:
        out.extend(pyth_lines)

    cg_lines = await _section_coingecko_reference(redis, bases)
    if cg_lines:
        out.extend(cg_lines)

    cz_state = await _section_coinalyze_state(redis, bases)
    if cz_state:
        out.append("### Coinalyze (MCP / slot cache)\n" + "\n".join(cz_state))

    cz = await _section_coinalyze(redis, bases)
    if cz:
        out.append("### Coinalyze (derivatives cache)\n" + "\n".join(cz))

    okx = await _section_okx_mcp(redis, bases)
    if okx:
        out.append("### OKX MCP (perp cache)\n" + "\n".join(okx))

    af = await _section_altfins(redis, bases)
    if af:
        out.extend(af)

    return out


async def _chunks_macro_system_health(redis: Redis) -> list[str]:
    out: list[str] = []
    fred = await _section_fred(redis)
    if fred:
        out.append("### FRED (macro cache)\n" + "\n".join(fred))
    fg = await _section_fear_greed(redis)
    if fg:
        out.append("### Sentiment benchmark (Alternative.me cache)\n" + "\n".join(fg))
    dl = await _section_defillama(redis)
    if dl:
        out.extend(dl)
    ops = await _section_operational_flags(redis)
    if ops:
        out.extend(ops)
    health = await _section_provider_health(redis)
    if health:
        out.extend(health)
    return out


async def _gather_context_chunk_sections(
    redis: Redis,
    settings: PolarisSettings,
    asset_focus: str,
    bases: list[str],
) -> list[str]:
    """Collect non-empty markdown sections (side-effect: reads Redis)."""
    chunks = await _chunks_asset_venue_and_derivatives(redis, settings, asset_focus, bases)
    chunks.extend(await _chunks_macro_system_health(redis))
    return chunks


async def build_omnibox_redis_provider_context(
    redis: Redis,
    settings: PolarisSettings,
    asset: str,
) -> str | None:
    """Merge Redis caches into a markdown-flavoured block for the chat user message."""
    asset_focus = (asset.strip() or "BTC").upper()
    bases = _unique_base_symbols_from_tokens(polaris_signal_wire_tokens(asset_focus))
    chunks = await _gather_context_chunk_sections(redis, settings, asset_focus, bases)
    if not chunks:
        return None

    header = (
        "The following lines are **read-only snapshots** from ATLAS Redis — every "
        "in-tree provider cache the API knows how to read: POLARIS signals, Pyth, "
        "CoinGecko cross-price, Bitget venue row (if present), Coinalyze "
        "(funding, OI, 30m liquidations & long/short), OKX MCP (funding, OI, L/S, "
        "recent liquidations), Altfins, FRED, Alternative.me Fear&Greed, DeFi Llama "
        "(stablecoins, top protocol TVLs, top yield pools), and rolling **provider "
        "health** scores for all feeds in the dashboard registry.\n"
        "Dune / Nansen / Hyperliquid tracker data is normally folded into the scored "
        "signal narrative — use the signal + memory sections for those themes unless "
        "your deployment adds extra Redis mirrors.\n"
        "Treat missing lines as 'no cache yet', not proof that a market fact is false.\n"
    )
    body = "\n\n".join(chunks)
    text = header + "\n" + body
    if len(text) > _MAX_CONTEXT_CHARS:
        text = text[: _MAX_CONTEXT_CHARS] + "\n… [context truncated]"
    return text
