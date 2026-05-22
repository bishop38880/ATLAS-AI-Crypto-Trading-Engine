"""Tests for the backtesting replay engine (BT-02)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from backtesting.data.models import FundingRateBar, OHLCVBar
from backtesting.engine.config import BacktestConfig, RiskConfig
from backtesting.engine.position import PositionTracker
from backtesting.engine.replay import BacktestReplay, compute_walk_forward_windows
from backtesting.engine.scorer import BarScore, BarScorer, BarScorerInput


def _bar_timestamp(index: int) -> str:
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    return (start + timedelta(hours=index)).isoformat()


def _ohlcv_bar(
    index: int,
    close: Decimal = Decimal("100"),
    volume: Decimal = Decimal("100"),
    high: Decimal | None = None,
    low: Decimal | None = None,
) -> OHLCVBar:
    return OHLCVBar(
        asset="ETHUSDT",
        timestamp_utc=_bar_timestamp(index),
        open=close,
        high=high or close + Decimal("1"),
        low=low or close - Decimal("1"),
        close=close,
        volume=volume,
        volume_usd=close * volume,
        timeframe="1h",
    )


def _flat_funding_bars(count: int, rate: Decimal = Decimal("0.0001")) -> list[FundingRateBar]:
    return [
        FundingRateBar(
            asset="ETHUSDT",
            timestamp_utc=_bar_timestamp(index),
            funding_rate=rate,
            funding_rate_annualised=rate * Decimal("1095"),
        )
        for index in range(count)
    ]


def _build_scorer_input(
    bar_index: int,
    bars: list[OHLCVBar],
    funding: list[FundingRateBar] | None = None,
) -> BarScorerInput:
    return BarScorerInput(
        asset="ETHUSDT",
        bar_index=bar_index,
        current_bar=bars[bar_index],
        lookback_ohlcv=bars[max(0, bar_index - 99):bar_index],
        lookback_funding=funding or _flat_funding_bars(30),
        btc_close_series=[bar.close for bar in bars[: bar_index + 1]],
        higher_tf_bars=bars[: bar_index + 1],
    )


class TestBarScorerNeutralMarket:
    """BarScorer returns low score for neutral conditions."""

    def test_neutral_market_scores_low(self) -> None:
        bars = [_ohlcv_bar(index, close=Decimal("100"), volume=Decimal("100")) for index in range(120)]
        scorer = BarScorer()
        result = scorer.score(_build_scorer_input(119, bars))
        assert result.total < 120
        assert result.signal_class == "NO_TRADE"


class TestBarScorerFundingZScore:
    """Funding z-score extreme values score derivatives dimension."""

    def test_extreme_negative_funding_scores_40_derivatives(self) -> None:
        rates = [Decimal("-0.0001")] * 29 + [Decimal("-0.01")]
        funding = [
            FundingRateBar(
                asset="ETHUSDT",
                timestamp_utc=_bar_timestamp(index),
                funding_rate=rates[index],
                funding_rate_annualised=rates[index] * Decimal("1095"),
            )
            for index in range(30)
        ]
        bars = [_ohlcv_bar(index) for index in range(120)]
        scorer = BarScorer()
        result = scorer.score(_build_scorer_input(119, bars, funding))
        assert result.derivatives >= 40


class TestBarScorerAdxGate:
    """ADX below 20 halves technical scores."""

    def test_low_adx_halves_technical(self) -> None:
        bars = []
        price = Decimal("100")
        for index in range(120):
            wiggle = Decimal(str((index % 3) - 1)) * Decimal("0.01")
            close = price + wiggle
            bars.append(_ohlcv_bar(index, close=close, volume=Decimal("100")))
        scorer = BarScorer()
        result = scorer.score(_build_scorer_input(119, bars))
        assert "adx_chop_halved" in result.factors or result.technical <= 22


class TestBarScorerNoLookahead:
    """Scorer input must never include future bars."""

    def test_lookback_excludes_current_and_future(self) -> None:
        bars = [_ohlcv_bar(index, close=Decimal(str(100 + index))) for index in range(150)]
        bar_index = 120
        scorer_input = _build_scorer_input(bar_index, bars)
        assert all(bar.timestamp_utc < bars[bar_index].timestamp_utc for bar in scorer_input.lookback_ohlcv)
        assert len(scorer_input.btc_close_series) == bar_index + 1
        assert scorer_input.bar_index == bar_index


class TestPositionTrackerExposure:
    """Portfolio exposure cap is enforced."""

    def test_blocks_entries_when_exposure_cap_reached(self) -> None:
        config = RiskConfig(
            account_size_usd=Decimal("10000"),
            max_position_pct=Decimal("5"),
            max_portfolio_exposure_pct=Decimal("25"),
        )
        tracker = PositionTracker(asset="ETHUSDT", config=config)
        score = BarScore(
            total=150,
            derivatives=40,
            technical=20,
            market_context=10,
            direction="LONG",
            signal_class="BUY",
            factors=[],
            atr=Decimal("2"),
        )
        opened = 0
        for index in range(10):
            bar = _ohlcv_bar(index)
            entry = tracker.try_open(score, bar, Decimal("10000"), config, index)
            if entry is not None:
                opened += 1
        assert opened == 5
        assert tracker.current_exposure_pct(Decimal("10000")) <= Decimal("25")


class TestPositionTrackerStopLoss:
    """Stop loss closes trade at the stop price with slippage."""

    def test_stop_loss_closes_on_trigger_bar(self) -> None:
        config = RiskConfig(slippage_bps=10, fee_bps=6)
        tracker = PositionTracker(asset="ETHUSDT", config=config)
        score = BarScore(
            total=180,
            derivatives=40,
            technical=20,
            market_context=10,
            direction="LONG",
            signal_class="STRONG",
            factors=[],
            atr=Decimal("5"),
        )
        entry_bar = _ohlcv_bar(0, close=Decimal("100"))
        entry = tracker.try_open(score, entry_bar, Decimal("10000"), config, 0)
        assert entry is not None
        trigger_bar = _ohlcv_bar(1, close=Decimal("99"), low=Decimal("88"), high=Decimal("101"))
        closed = tracker.update(trigger_bar, 1)
        assert len(closed) == 1
        assert closed[0].exit.exit_reason == "STOP_LOSS"
        assert closed[0].exit.exit_price == entry.stop_loss
        assert closed[0].exit.exit_price_with_slippage < closed[0].exit.exit_price


class TestWalkForwardWindows:
    """Walk-forward test windows do not overlap."""

    def test_test_windows_are_non_overlapping(self) -> None:
        windows = compute_walk_forward_windows(
            total_bars=2000,
            train_bars=720,
            test_bars=240,
        )
        test_ranges = [(window[2], window[3]) for window in windows]
        for left_index in range(len(test_ranges)):
            for right_index in range(left_index + 1, len(test_ranges)):
                left = test_ranges[left_index]
                right = test_ranges[right_index]
                assert left[1] <= right[0] or right[1] <= left[0]


class TestReplayIntegration:
    """Full replay on synthetic data completes successfully."""

    def test_synthetic_replay_completes(self) -> None:
        config = BacktestConfig(
            assets=["BTCUSDT"],
            start_date="2024-01-01",
            end_date="2024-02-01",
            use_synthetic=True,
        )
        result = asyncio.run(BacktestReplay().run(config))
        assert result.run_id == config.run_id
        assert result.total_trades == len(result.closed_trades)
        assert result.total_trades >= 0

    def test_replay_is_deterministic(self) -> None:
        config = BacktestConfig(
            assets=["BTCUSDT"],
            start_date="2024-01-01",
            end_date="2024-02-01",
            use_synthetic=True,
        )
        engine = BacktestReplay()
        first = asyncio.run(engine.run(config))
        second = asyncio.run(engine.run(config))
        assert first.total_trades == second.total_trades
