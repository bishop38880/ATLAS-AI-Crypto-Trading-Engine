"""Map dashboard / autonomous ``data`` payloads into ``AllSignals``.

Used by ``ATLAS_SIMPLE_PIPELINE_MODE`` so the deterministic ``ConfluenceScoringEngine``
can run without spawning the full analyst ensemble.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from atlas.providers.helius.onchain_signals import (
    fetch_helius_onchain_signals,
    helius_flow_is_actionable,
)
from atlas.scoring.confluence import (
    AllSignals,
    DerivativesSignals,
    MarketContextSignals,
    OHLCVCandle,
    OnChainSignals,
    SentimentSignals,
    TechnicalSignals,
)


def _series_float(data: dict[str, Any], key: str, default: float = 0.0) -> float:
    raw = data.get(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _price_direction(close_series: list[float]) -> str:
    if len(close_series) < 15:
        return "flat"
    tail = close_series[-1]
    ref = close_series[-15]
    if tail > ref * 1.001:
        return "up"
    if tail < ref * 0.999:
        return "down"
    return "flat"


def _build_ohlcv_rows(data: dict[str, Any]) -> list[OHLCVCandle]:
    closes = data.get("close") or []
    highs = data.get("high") or []
    lows = data.get("low") or []
    opens = data.get("open") or []
    volumes = data.get("volume") or []
    if not isinstance(closes, list) or not closes:
        return []
    candles: list[OHLCVCandle] = []
    for idx, close_raw in enumerate(closes):
        close_px = Decimal(str(close_raw))
        high_px = Decimal(str(highs[idx])) if idx < len(highs) else close_px
        low_px = Decimal(str(lows[idx])) if idx < len(lows) else close_px
        open_px = Decimal(str(opens[idx])) if idx < len(opens) else close_px
        vol_px = Decimal(str(volumes[idx])) if idx < len(volumes) else Decimal("0")
        candles.append(
            OHLCVCandle(
                timestamp=float(idx),
                open=open_px,
                high=high_px,
                low=low_px,
                close=close_px,
                volume=vol_px,
            ),
        )
    return candles


def _onchain_blob(data: dict[str, Any]) -> OnChainSignals:
    snap = data.get("nansen_snapshot")
    whale_flow = Decimal("0")
    exch_flow = Decimal("0")
    if snap is not None:
        sm = getattr(snap, "smart_money_flow_24h", None)
        if sm is not None:
            whale_flow = getattr(sm, "net_flow_usd", Decimal("0"))
        exch = getattr(snap, "exchange_netflow", None)
        if exch is not None:
            exch_flow = getattr(exch, "netflow_usd", Decimal("0"))
    return OnChainSignals(
        whale_outflow_zscore=_series_float(data, "whale_zscore"),
        whale_inflow_usd=whale_flow,
        exchange_netflow_4h=exch_flow,
        active_addresses_zscore=_series_float(data, "active_addresses_zscore"),
        data_source="nansen",
    )


async def build_all_signals_from_market_payload_async(
    data: dict[str, Any],
    context: dict[str, Any],
    asset: str,
) -> AllSignals:
    """Construct ``AllSignals``; uses Helius on-chain for SOL/JUP when data exists."""
    onchain = await _resolve_onchain_signals(data, context, asset)
    return _assemble_all_signals(data, context, onchain)


def build_all_signals_from_market_payload(
    data: dict[str, Any],
    context: dict[str, Any],
    asset: str = "",
) -> AllSignals:
    """Sync bridge — Nansen fallback only (use async variant for Helius)."""
    onchain = _onchain_blob(data)
    if context.get("helius_onchain") is not None:
        raw = context["helius_onchain"]
        if isinstance(raw, OnChainSignals):
            onchain = raw
    return _assemble_all_signals(data, context, onchain)


async def _resolve_onchain_signals(
    data: dict[str, Any],
    context: dict[str, Any],
    asset: str,
) -> OnChainSignals:
    """Prefer Helius flow tracker for SOL/JUP; fall back to Nansen snapshot fields."""
    cached = context.get("helius_onchain")
    if isinstance(cached, OnChainSignals) and cached.data_source == "helius":
        return cached

    helius = await fetch_helius_onchain_signals(asset)
    if helius is not None and helius_flow_is_actionable(helius):
        return helius

    return _onchain_blob(data)


def _assemble_all_signals(
    data: dict[str, Any],
    context: dict[str, Any],
    onchain: OnChainSignals,
) -> AllSignals:
    """Build ``AllSignals`` from market payload and resolved on-chain slice."""
    closes = data.get("close") if isinstance(data.get("close"), list) else []
    close_nums = [float(x) for x in closes] if closes else []

    derivatives = DerivativesSignals(
        funding_rate_zscore=_series_float(data, "funding_zscore"),
        oi_change_4h=_series_float(data, "oi_change_pct"),
        oi_change_7d=_series_float(data, "oi_change_7d"),
        price_direction=_price_direction(close_nums),
        liquidation_imbalance=_series_float(data, "liquidation_imbalance"),
        cascade_active=bool(data.get("cascade_active", False)),
        basis_annualized=_series_float(data, "basis_annualised"),
        data_source=str(data.get("derivatives_source", "coinalyze+hydra")),
    )

    technical = TechnicalSignals(
        volume_ratio=_series_float(data, "volume_ratio", 1.0),
        rsi_14=_series_float(data, "rsi_14", 50.0),
        adx=_series_float(data, "adx", 15.0),
        macd_histogram=_series_float(data, "macd_histogram"),
        bb_position=_series_float(data, "bb_position", 0.5),
        ohlcv_30m=_build_ohlcv_rows(data),
        ohlcv_4h=[],
        orderbook=None,
        data_source=str(data.get("technical_source", "bitget")),
    )

    fg_ctx = context.get("fear_greed_score")
    fg_data = data.get("fear_greed_score")
    fg_score = int(fg_ctx) if fg_ctx is not None else int(fg_data or 50)

    sentiment = SentimentSignals(
        social_volume_zscore=_series_float(context, "vol_zscore"),
        polarity_percentile=_series_float(data, "polarity_percentile", 50.0),
        fear_greed_score=fg_score,
        influencer_velocity=_series_float(data, "influencer_velocity"),
        data_source=str(data.get("sentiment_source", "alternative_me")),
    )

    market_context = MarketContextSignals(
        volatility_regime=str(context.get("volatility_regime", "moderate")),
        btc_correlation_7d=_series_float(context, "btc_correlation_7d", 0.5),
        fear_greed_index=fg_score,
        btc_dominance_pct=_series_float(context, "btc_dominance_pct", 45.0),
        data_source=str(context.get("market_context_source", "coingecko")),
    )

    return AllSignals(
        derivatives=derivatives,
        onchain=onchain,
        technical=technical,
        sentiment=sentiment,
        market_context=market_context,
    )
