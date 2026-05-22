from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

import msgspec
from loguru import logger

from atlas.scoring.scoring_weights import (
    CATEGORY_MAX_POINTS,
    TOTAL_POINTS,
    classify_signal_strength,
    get_position_size_pct,
)


# ---------------------------------------------------------------------------
# Schema Types — msgspec Structs Only
# ---------------------------------------------------------------------------

class DerivativesSignals(msgspec.Struct, frozen=True):
    funding_rate_zscore: float
    oi_change_4h: float
    oi_change_7d: float
    price_direction: str  # "up" | "down" | "flat"
    liquidation_imbalance: float
    cascade_active: bool
    basis_annualized: float
    data_source: str  # e.g., "coinalyze+hydra"
    coinbase_premium_contribution: float = 0.0  # ±8 pts from Redis premium service
    coinbase_premium_signal: str = "n/a"


class OnChainSignals(msgspec.Struct, frozen=True):
    whale_outflow_zscore: float
    whale_inflow_usd: Decimal
    exchange_netflow_4h: Decimal
    active_addresses_zscore: float
    data_source: str  # "nansen" | "helius"


class OHLCVCandle(msgspec.Struct, frozen=True):
    timestamp: float
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


class TechnicalSignals(msgspec.Struct, frozen=True):
    volume_ratio: float
    rsi_14: float
    adx: float
    macd_histogram: float
    bb_position: float
    ohlcv_30m: list[OHLCVCandle]
    ohlcv_4h: list[OHLCVCandle]
    orderbook: dict | None
    data_source: str  # "bitget"


class SentimentSignals(msgspec.Struct, frozen=True):
    social_volume_zscore: float
    polarity_percentile: float
    fear_greed_score: int
    influencer_velocity: float
    data_source: str  # "alternative_me"


class MarketContextSignals(msgspec.Struct, frozen=True):
    volatility_regime: str  # "low_trending" | "moderate" | "high_uni" | "high_chaotic"
    btc_correlation_7d: float
    fear_greed_index: int
    btc_dominance_pct: float
    data_source: str


class AllSignals(msgspec.Struct, frozen=True):
    derivatives: DerivativesSignals
    onchain: OnChainSignals
    technical: TechnicalSignals
    sentiment: SentimentSignals
    market_context: MarketContextSignals


class RegimeContext(msgspec.Struct, frozen=True):
    regime_label: str
    multiplier_category: str


class CategoryResult(msgspec.Struct, frozen=True):
    score: int
    max: int
    factors: list[str]


class ShadowMetrics(msgspec.Struct, frozen=True):
    vwap_deviation_pct: float | None
    orderbook_imbalance: float | None
    ob_spread_bps: float | None
    ob_depth_usd_1pct: float | None
    sr_nearest_resistance_pct: float | None
    sr_nearest_support_pct: float | None
    sr_ratio: float | None
    sr_level_strength: int | None
    atr_normalized_range: float | None
    atr_normalized_direction: float | None
    atr_14_value: float | None


class ConfluenceResult(msgspec.Struct, frozen=True):
    total_score: int  # 0-220
    breakdown: dict[str, CategoryResult]
    regime_multiplier: float
    sentiment_was_gated: bool
    signal_strength: str  # "STRONG" | "MODERATE" | "WEAK" | "NO_TRADE"
    position_size_pct: float  # dimensionless; Decimal cast occurs downstream in PROMETHEUS
    bias: str  # "long" | "short" | "neutral"
    reasoning: str
    data_source_map: dict[str, str]  # provider attribution per category
    shadow_metrics: ShadowMetrics
    version: str


# ---------------------------------------------------------------------------
# Core Engine
# ---------------------------------------------------------------------------

class ConfluenceScoringEngine:
    """
    Deterministic 220-point scoring engine.
    Same inputs ALWAYS produce same outputs.
    The 220-point architecture is sacred. Overlays never modify scores — only sizing.
    """

    VERSION = "3.0"
    TOTAL_MAX = TOTAL_POINTS

    # v6.1 ceilings — imported from ``scoring_weights`` (single source of truth).
    CATEGORY_MAXES = dict(CATEGORY_MAX_POINTS)

    def __init__(self, db_pool: Any = None) -> None:
        self._shadow_queue: asyncio.Queue[AllSignals] = asyncio.Queue(maxsize=1000)
        self._worker_task: asyncio.Task[None] | None = None
        self._db_pool = db_pool

    def start_worker(self) -> None:
        """Start the background worker for shadow metrics."""
        if self._worker_task is None:
            self._worker_task = asyncio.create_task(self._shadow_worker_loop())

    async def _shadow_worker_loop(self) -> None:
        """Background loop to process shadow metrics."""
        batch: list[ShadowMetrics] = []
        while True:
            try:
                try:
                    signals = await asyncio.wait_for(self._shadow_queue.get(), timeout=1.0)
                    metrics = await asyncio.to_thread(self._compute_shadow_metrics, signals)
                    batch.append(metrics)
                    self._shadow_queue.task_done()
                except asyncio.TimeoutError:
                    pass

                if (len(batch) >= 100 or (batch and self._shadow_queue.empty())):
                    await self._flush_shadow_metrics(batch)
                    batch = []
            except asyncio.CancelledError:
                if batch:
                    await self._flush_shadow_metrics(batch)
                raise
            except Exception as e:
                logger.error("shadow_metrics_worker_error | error={}", str(e))

    async def _flush_shadow_metrics(self, batch: list[ShadowMetrics]) -> None:
        if not self._db_pool or not batch:
            return
        query = """
            INSERT INTO shadow_metrics (
                vwap_deviation_pct, orderbook_imbalance, ob_spread_bps, ob_depth_usd_1pct,
                sr_nearest_resistance_pct, sr_nearest_support_pct, sr_ratio,
                sr_level_strength, atr_normalized_range, atr_normalized_direction, atr_14_value
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
        """
        records = [
            (
                m.vwap_deviation_pct, m.orderbook_imbalance, m.ob_spread_bps, m.ob_depth_usd_1pct,
                m.sr_nearest_resistance_pct, m.sr_nearest_support_pct, m.sr_ratio,
                m.sr_level_strength, m.atr_normalized_range, m.atr_normalized_direction, m.atr_14_value
            ) for m in batch
        ]
        try:
            async with self._db_pool.acquire() as conn:
                await conn.executemany(query, records)
        except Exception as e:
            logger.error("failed_to_flush_shadow_metrics | error={}", str(e))

    async def stop_worker(self) -> None:
        """Stop the background worker."""
        if self._worker_task is not None:
            try:
                await asyncio.wait_for(self._shadow_queue.join(), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("shadow_queue_drain_timeout")
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                raise
            self._worker_task = None

    async def calculate(
        self, signals: AllSignals, regime: RegimeContext
    ) -> ConfluenceResult:
        """Main entry point for confluence score calculation."""
        sentiment_active = self._sentiment_gate_active(signals.sentiment)
        breakdown = self._score_all_categories(signals, regime, sentiment_active)

        raw_total = sum(v.score for v in breakdown.values())
        regime_multiplier = self._get_regime_multiplier(
            breakdown["market_context"], regime
        )
        adjusted_total = min(int(raw_total * regime_multiplier), self.TOTAL_MAX)

        try:
            self._shadow_queue.put_nowait(signals)
        except asyncio.QueueFull:
            logger.warning("shadow_queue_full_dropping_metrics")

        logger.info(
            "confluence_calculated | total={} | raw={} | multiplier={} | version={}",
            adjusted_total, raw_total, regime_multiplier, self.VERSION,
        )

        return self._build_confluence_result(
            breakdown, signals, adjusted_total, regime_multiplier, sentiment_active,
        )

    def _build_confluence_result(
        self,
        breakdown: dict[str, CategoryResult],
        signals: AllSignals,
        adjusted_total: int,
        regime_multiplier: float,
        sentiment_active: bool,
    ) -> ConfluenceResult:
        """Assemble the ConfluenceResult from computed components."""
        return ConfluenceResult(
            total_score=adjusted_total,
            breakdown=breakdown,
            regime_multiplier=regime_multiplier,
            sentiment_was_gated=not sentiment_active,
            signal_strength=classify_signal_strength(adjusted_total),
            position_size_pct=get_position_size_pct(adjusted_total),
            bias=self._determine_bias(signals),
            reasoning=self._generate_reasoning(breakdown, adjusted_total),
            data_source_map=_build_data_source_map(signals),
            shadow_metrics=_build_default_shadow_metrics(),
            version=self.VERSION,
        )

    def _sentiment_gate_active(self, s: SentimentSignals) -> bool:
        """Unlock sentiment only on tails or a social volume spike (event context)."""
        extreme_fg = s.fear_greed_score >= 75 or s.fear_greed_score <= 25
        extreme_pol = s.polarity_percentile <= 25.0 or s.polarity_percentile >= 75.0
        vol_spike = s.social_volume_zscore > 2.0
        return extreme_fg or extreme_pol or vol_spike

    def _score_all_categories(
        self,
        signals: AllSignals,
        regime: RegimeContext,
        sentiment_active: bool,
    ) -> dict[str, CategoryResult]:
        """Score each category individually."""
        out: dict[str, CategoryResult] = {}
        out["derivatives"] = self._score_derivatives(signals.derivatives)
        out["onchain"] = self._score_onchain(signals.onchain)
        out["technical"] = self._score_technical(signals.technical)

        if sentiment_active:
            out["sentiment"] = self._score_sentiment(signals.sentiment)
        else:
            out["sentiment"] = CategoryResult(
                score=0,
                max=self.CATEGORY_MAXES["sentiment"],
                factors=["GATED: no social volume spike or extreme sentiment detected"],
            )

        out["market_context"] = self._score_market_context(
            signals.market_context, regime
        )
        return out

    def _score_derivatives(self, d: DerivativesSignals) -> CategoryResult:
        """Compute Category 1: Derivatives Intelligence (75 pts)."""
        score = 0
        factors: list[str] = []

        fund_pts = self._score_funding(d.funding_rate_zscore)
        score += fund_pts
        factors.append(f"funding_zscore ({d.funding_rate_zscore:.2f} SD): {fund_pts}/22pts")

        oi_pts = self._score_oi_composite(d.oi_change_4h, d.oi_change_7d, d.price_direction)
        score += oi_pts
        factors.append(f"oi_composite: {oi_pts}/22pts")

        liq_pts = self._score_liquidation(d.liquidation_imbalance, d.cascade_active)
        score += liq_pts
        factors.append(f"liquidation_imbalance: {liq_pts}/18pts")

        basis_pts = self._score_basis(d.basis_annualized)
        score += basis_pts
        factors.append(f"basis_annualized: {basis_pts}/13pts")

        premium_pts = int(round(d.coinbase_premium_contribution))
        if premium_pts != 0 or d.coinbase_premium_signal != "n/a":
            score += premium_pts
            factors.append(
                f"coinbase_premium ({d.coinbase_premium_signal}): {premium_pts}/8pts",
            )

        cap = self.CATEGORY_MAXES["derivatives"]
        return CategoryResult(score=min(score, cap), max=cap, factors=factors)

    def _score_onchain(self, o: OnChainSignals) -> CategoryResult:
        """Compute Category 2: Whale / On-Chain Flow (65 pts)."""
        score = 0
        factors: list[str] = []

        out_pts = 23 if o.whale_outflow_zscore > 2.0 else (16 if o.whale_outflow_zscore > 1.0 else 6)
        if o.whale_outflow_zscore < -2.0:
            out_pts = 0
        score += out_pts
        factors.append(f"whale_outflow ({o.whale_outflow_zscore:.2f} SD): {out_pts}/23pts")

        in_pts = 19 if o.whale_inflow_usd > Decimal("1000000") else (12 if o.whale_inflow_usd > Decimal("500000") else 6)
        if o.whale_inflow_usd < Decimal("0"):
            in_pts = 0
        score += in_pts
        factors.append(f"whale_inflow: {in_pts}/19pts")

        net_pts = 13 if o.exchange_netflow_4h < Decimal("-1000000") else (9 if o.exchange_netflow_4h < Decimal("0") else (5 if o.exchange_netflow_4h == Decimal("0") else 2))
        score += net_pts
        factors.append(f"exchange_netflow: {net_pts}/13pts")

        act_pts = 10 if o.active_addresses_zscore > 2.0 else (6 if o.active_addresses_zscore > 1.0 else (3 if o.active_addresses_zscore > -1.0 else 0))
        score += act_pts
        factors.append(f"active_addresses: {act_pts}/10pts")

        cap = self.CATEGORY_MAXES["onchain"]
        return CategoryResult(score=min(score, cap), max=cap, factors=factors)

    def _score_technical(self, t: TechnicalSignals) -> CategoryResult:
        """Technical filter only — ADX + Bollinger boundary context (15 pts)."""
        score = 0
        factors: list[str] = []

        adx_pts = 8 if t.adx > 30.0 else (5 if t.adx >= 20.0 else 2)
        score += adx_pts
        factors.append(f"adx_trend_filter: {adx_pts}/8pts")

        if t.bb_position < 0.1 or t.bb_position > 0.9:
            bb_pts = 7
        elif t.bb_position < 0.25 or t.bb_position > 0.75:
            bb_pts = 4
        else:
            bb_pts = 1
        score += bb_pts
        factors.append(f"bb_price_boundary: {bb_pts}/7pts")

        cap = self.CATEGORY_MAXES["technical"]
        return CategoryResult(score=min(score, cap), max=cap, factors=factors)

    def _score_sentiment(self, s: SentimentSignals) -> CategoryResult:
        """Compute Category 3: Social / Sentiment — tail emphasis (35 pts)."""
        score = 0
        factors: list[str] = []

        vol_pts = 14 if s.social_volume_zscore > 3.0 else (9 if s.social_volume_zscore > 2.0 else (5 if s.social_volume_zscore > 1.0 else 0))
        score += vol_pts
        factors.append(f"social_volume: {vol_pts}/14pts")

        pol_pts = 10 if s.polarity_percentile < 25.0 or s.polarity_percentile > 75.0 else (5 if s.polarity_percentile < 40.0 or s.polarity_percentile > 60.0 else 0)
        score += pol_pts
        factors.append(f"polarity_percentile: {pol_pts}/10pts")

        fg_pts = 7 if s.fear_greed_score >= 75 or s.fear_greed_score <= 25 else (4 if s.fear_greed_score >= 60 or s.fear_greed_score <= 40 else (2 if s.fear_greed_score >= 55 or s.fear_greed_score <= 45 else 0))
        score += fg_pts
        factors.append(f"fear_greed_score: {fg_pts}/7pts")

        vel_pts = 4 if s.influencer_velocity > 2.0 else (2 if s.influencer_velocity > 1.0 else 0)
        score += vel_pts
        factors.append(f"influencer_velocity: {vel_pts}/4pts")

        cap = self.CATEGORY_MAXES["sentiment"]
        return CategoryResult(score=min(score, cap), max=cap, factors=factors)

    def _score_market_context(
        self, m: MarketContextSignals, regime: RegimeContext
    ) -> CategoryResult:
        """Compute Category 4: Macro / Regime Context (30 pts)."""
        score = 0
        factors: list[str] = []

        vol_pts = 11 if m.volatility_regime == "low_trending" else (8 if m.volatility_regime == "high_uni" else (7 if m.volatility_regime == "moderate" else 3))
        score += vol_pts
        factors.append(f"volatility_regime: {vol_pts}/11pts")

        btc_pts = 8 if m.btc_correlation_7d < 0.4 else (5 if m.btc_correlation_7d <= 0.7 else 2)
        score += btc_pts
        factors.append(f"btc_correlation_7d: {btc_pts}/8pts")

        fear_pts = 6 if m.fear_greed_index < 25 or m.fear_greed_index > 75 else 3
        score += fear_pts
        factors.append(f"fear_greed_gate: {fear_pts}/6pts")

        dom_pts = 5 if m.btc_dominance_pct > 50.0 or m.btc_dominance_pct < 40.0 else 2
        score += dom_pts
        factors.append(f"btc_dominance_macro: {dom_pts}/5pts")

        cap = self.CATEGORY_MAXES["market_context"]
        return CategoryResult(score=min(score, cap), max=cap, factors=factors)

    # --- Sub-Signal Helpers ---

    def _score_funding(self, zscore: float) -> int:
        if abs(zscore) >= 2.5:
            return 22
        if abs(zscore) >= 1.5:
            return 11
        return 0

    def _score_oi_composite(self, oi_4h: float, oi_7d: float, direction: str) -> int:
        if oi_4h > 1.05 and oi_7d > 1.10:
            return 22
        if oi_4h > 1.02:
            return 14
        if oi_4h > 0.98:
            return 7
        if oi_4h > 0.90:
            return 3
        return 0

    def _score_liquidation(self, imbalance: float, cascade_active: bool) -> int:
        if cascade_active:
            return 18
        if imbalance > 1.5 or imbalance < 0.66:
            return 12
        return 5

    def _score_basis(self, basis: float) -> int:
        if basis > 15.0 or basis < -5.0:
            return 13
        if basis < 0.0:
            return 10
        if basis > 5.0:
            return 8
        return 5

    # --- Output Helpers ---

    def _get_regime_multiplier(self, ctx_res: CategoryResult, r: RegimeContext) -> float:
        """Determine regime multiplier based on market-context category."""
        joined = "".join(ctx_res.factors)
        if "low_trending" in joined and "btc_correlation_7d: 8/8pts" in joined:
            return 1.15
        if "high_chaotic" in joined and "fear_greed_gate: 6/6pts" in joined:
            return 0.75
        if r.multiplier_category == "fear_greed_neutral_trending":
            return 1.05
        return 1.0

    def _determine_bias(self, signals: AllSignals) -> str:
        return "neutral"  # Stub implementation

    def _generate_reasoning(self, breakdown: dict[str, CategoryResult], total: int) -> str:
        return f"Confluence score {total}/220. Active categories: {', '.join(k for k, v in breakdown.items() if v.score > 0)}."

    # --- Shadow Metrics ---

    def _compute_shadow_metrics(self, signals: AllSignals) -> ShadowMetrics:
        """Observation only — never affects score."""
        return ShadowMetrics(
            vwap_deviation_pct=0.0,
            orderbook_imbalance=0.5,
            ob_spread_bps=1.0,
            ob_depth_usd_1pct=1000000.0,
            sr_nearest_resistance_pct=1.0,
            sr_nearest_support_pct=-1.0,
            sr_ratio=0.5,
            sr_level_strength=3,
            atr_normalized_range=1.0,
            atr_normalized_direction=0.0,
            atr_14_value=100.0,
        )


def _build_data_source_map(signals: AllSignals) -> dict[str, str]:
    """Build a map of category to data source from AllSignals."""
    return {
        "derivatives": signals.derivatives.data_source,
        "onchain": signals.onchain.data_source,
        "technical": signals.technical.data_source,
        "sentiment": signals.sentiment.data_source,
        "market_context": signals.market_context.data_source,
    }


def _build_default_shadow_metrics() -> ShadowMetrics:
    """Return default empty shadow metrics for the hot path."""
    return ShadowMetrics(
        vwap_deviation_pct=None,
        orderbook_imbalance=None,
        ob_spread_bps=None,
        ob_depth_usd_1pct=None,
        sr_nearest_resistance_pct=None,
        sr_nearest_support_pct=None,
        sr_ratio=None,
        sr_level_strength=None,
        atr_normalized_range=None,
        atr_normalized_direction=None,
        atr_14_value=None,
    )

