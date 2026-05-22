import asyncio
from decimal import Decimal

import pytest

from atlas.scoring.confluence import (
    AllSignals,
    ConfluenceScoringEngine,
    DerivativesSignals,
    MarketContextSignals,
    OnChainSignals,
    RegimeContext,
    SentimentSignals,
    TechnicalSignals,
)


def _default_derivatives() -> DerivativesSignals:
    return DerivativesSignals(
        funding_rate_zscore=0.0, oi_change_4h=1.0, oi_change_7d=1.0,
        price_direction="flat", liquidation_imbalance=1.0,
        cascade_active=False, basis_annualized=1.0,
        data_source="coinalyze+hydra",
    )

def _default_onchain() -> OnChainSignals:
    return OnChainSignals(
        whale_outflow_zscore=0.0, whale_inflow_usd=Decimal("0"),
        exchange_netflow_4h=Decimal("0"), active_addresses_zscore=0.0,
        data_source="nansen",
    )

def _default_technical() -> TechnicalSignals:
    return TechnicalSignals(
        volume_ratio=1.0, rsi_14=50.0, adx=15.0, macd_histogram=0.0,
        bb_position=0.5, ohlcv_30m=[], ohlcv_4h=[], orderbook=None,
        data_source="bitget",
    )

def _default_sentiment() -> SentimentSignals:
    return SentimentSignals(
        social_volume_zscore=0.0, polarity_percentile=50.0,
        fear_greed_score=50, influencer_velocity=0.0,
        data_source="alternative_me",
    )

def _default_market_context() -> MarketContextSignals:
    return MarketContextSignals(
        volatility_regime="moderate", btc_correlation_7d=0.5,
        fear_greed_index=50, btc_dominance_pct=45.0,
        data_source="coingecko",
    )


@pytest.fixture
def base_signals() -> AllSignals:
    return AllSignals(
        derivatives=_default_derivatives(),
        onchain=_default_onchain(),
        technical=_default_technical(),
        sentiment=_default_sentiment(),
        market_context=_default_market_context(),
    )


@pytest.fixture
def regime_ctx() -> RegimeContext:
    return RegimeContext(regime_label="normal", multiplier_category="normal_conditions")


@pytest.mark.asyncio
async def test_confluence_engine_sentiment_gate(base_signals: AllSignals, regime_ctx: RegimeContext) -> None:
    engine = ConfluenceScoringEngine()
    result = await engine.calculate(base_signals, regime_ctx)
    assert result.sentiment_was_gated is True
    assert result.breakdown["sentiment"].score == 0

    active_sentiment = SentimentSignals(
        social_volume_zscore=3.5,
        polarity_percentile=50.0,
        fear_greed_score=50,
        influencer_velocity=0.0,
        data_source="alternative_me",
    )
    active_signals = AllSignals(
        derivatives=base_signals.derivatives,
        onchain=base_signals.onchain,
        technical=base_signals.technical,
        sentiment=active_sentiment,
        market_context=base_signals.market_context,
    )
    result2 = await engine.calculate(active_signals, regime_ctx)
    assert result2.sentiment_was_gated is False
    assert result2.breakdown["sentiment"].score > 0


@pytest.mark.asyncio
async def test_confluence_data_source_map(base_signals: AllSignals, regime_ctx: RegimeContext) -> None:
    engine = ConfluenceScoringEngine()
    result = await engine.calculate(base_signals, regime_ctx)
    assert result.data_source_map["derivatives"] == "coinalyze+hydra"
    assert result.data_source_map["onchain"] == "nansen"
    assert result.data_source_map["technical"] == "bitget"
    assert result.data_source_map["sentiment"] == "alternative_me"
    assert result.data_source_map["market_context"] == "coingecko"


@pytest.mark.asyncio
async def test_funding_gate_non_linear(base_signals: AllSignals, regime_ctx: RegimeContext) -> None:
    engine = ConfluenceScoringEngine()

    high_fund = DerivativesSignals(
        funding_rate_zscore=2.6,
        oi_change_4h=1.0,
        oi_change_7d=1.0,
        price_direction="flat",
        liquidation_imbalance=1.0,
        cascade_active=False,
        basis_annualized=1.0,
        data_source="coinalyze+hydra",
    )
    high_signals = AllSignals(
        derivatives=high_fund,
        onchain=base_signals.onchain,
        technical=base_signals.technical,
        sentiment=base_signals.sentiment,
        market_context=base_signals.market_context,
    )

    res = await engine.calculate(high_signals, regime_ctx)
    factors = "".join(res.breakdown["derivatives"].factors)
    assert "22pts" in factors


@pytest.mark.asyncio
async def test_shadow_metrics_computed(base_signals: AllSignals, regime_ctx: RegimeContext) -> None:
    """Hot-path shadow metrics are default None (async worker populates later)."""
    engine = ConfluenceScoringEngine()
    result = await engine.calculate(base_signals, regime_ctx)
    assert result.shadow_metrics is not None
    assert result.shadow_metrics.orderbook_imbalance is None
    assert result.shadow_metrics.vwap_deviation_pct is None


@pytest.mark.asyncio
async def test_derivatives_includes_coinbase_premium_points(
    base_signals: AllSignals,
    regime_ctx: RegimeContext,
) -> None:
    """Positive Coinbase premium adds points within the derivatives category cap."""
    derivatives = DerivativesSignals(
        funding_rate_zscore=0.0,
        oi_change_4h=0.0,
        oi_change_7d=0.0,
        price_direction="flat",
        liquidation_imbalance=0.0,
        cascade_active=False,
        basis_annualized=0.0,
        data_source="test",
        coinbase_premium_contribution=6.0,
        coinbase_premium_signal="mild_us_buying",
    )
    all_signals = AllSignals(
        derivatives=derivatives,
        onchain=base_signals.onchain,
        technical=base_signals.technical,
        sentiment=base_signals.sentiment,
        market_context=base_signals.market_context,
    )
    engine = ConfluenceScoringEngine()
    result = await engine.calculate(all_signals, regime_ctx)
    deriv = result.breakdown["derivatives"]
    assert deriv.score >= 6
    assert any("coinbase_premium" in factor for factor in deriv.factors)

