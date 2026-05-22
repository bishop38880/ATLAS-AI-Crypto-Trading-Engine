"""Core walk-forward replay engine for backtesting."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import polars as pl
from pydantic import BaseModel, Field

from backtesting.data.db import BacktestDB
from backtesting.data.models import FundingRateBar, OHLCVBar
from backtesting.data.synthetic import SyntheticDataGenerator
from backtesting.engine.config import BacktestConfig
from backtesting.engine.events import ReplayEvent
from backtesting.engine.position import ClosedTrade, PositionTracker
from backtesting.engine.scorer import BarScorer, BarScorerInput

_MIN_WARMUP_BARS = 100
_SYNTHETIC_BAR_COUNT = 1500
_SYNTHETIC_SEED = 42


class BacktestResult(BaseModel, frozen=True):
    """Aggregate output from a completed backtest run."""

    run_id: str
    total_trades: int
    closed_trades: list[ClosedTrade]
    events: list[ReplayEvent]
    final_account_value: Decimal
    assets_processed: list[str]


class BacktestReplay:
    """Walks forward through historical bars and simulates ATLAS trading."""

    def __init__(self, db: BacktestDB | None = None) -> None:
        self._db = db
        self._scorer = BarScorer()

    async def run(
        self,
        config: BacktestConfig,
        on_progress: Callable[[int, int, str], None] | None = None,
    ) -> BacktestResult:
        """Run the full backtest and return aggregated results."""
        all_trades: list[ClosedTrade] = []
        all_events: list[ReplayEvent] = []
        account_value = config.risk.account_size_usd
        total_bars = 0
        bars_complete = 0
        for asset in config.assets:
            bundle = await self._load_asset_data(asset, config)
            asset_trades, asset_events, account_value = self._replay_asset(
                asset=asset,
                ohlcv=bundle["ohlcv"],
                funding=bundle["funding"],
                btc_closes=bundle["btc_closes"],
                higher_tf=bundle["higher_tf"],
                config=config,
                account_value=account_value,
                on_progress=on_progress,
                bars_complete_offset=bars_complete,
                total_bars_offset=total_bars,
            )
            all_trades.extend(asset_trades)
            all_events.extend(asset_events)
            bars_complete += max(len(bundle["ohlcv"]) - _MIN_WARMUP_BARS, 0)
            total_bars += max(len(bundle["ohlcv"]) - _MIN_WARMUP_BARS, 0)
        return BacktestResult(
            run_id=config.run_id,
            total_trades=len(all_trades),
            closed_trades=all_trades,
            events=all_events,
            final_account_value=account_value,
            assets_processed=list(config.assets),
        )

    async def _load_asset_data(
        self,
        asset: str,
        config: BacktestConfig,
    ) -> dict[str, Any]:
        """Load OHLCV, funding, and BTC reference data."""
        if config.use_synthetic:
            return await asyncio.to_thread(_load_synthetic_bundle, asset, config)
        return await asyncio.to_thread(_load_db_bundle, self._db, asset, config)

    def _replay_asset(
        self,
        asset: str,
        ohlcv: pl.DataFrame,
        funding: pl.DataFrame,
        btc_closes: list[Decimal],
        higher_tf: pl.DataFrame,
        config: BacktestConfig,
        account_value: Decimal,
        on_progress: Callable[[int, int, str], None] | None,
        bars_complete_offset: int,
        total_bars_offset: int,
    ) -> tuple[list[ClosedTrade], list[ReplayEvent], Decimal]:
        """Replay a single asset and return trades, events, and updated account."""
        bars = _dataframe_to_ohlcv_bars(ohlcv, asset, config.timeframe)
        funding_bars = _dataframe_to_funding_bars(funding, asset)
        higher_bars = _dataframe_to_ohlcv_bars(higher_tf, asset, config.higher_timeframe)
        tracker = PositionTracker(asset=asset, config=config.risk)
        events: list[ReplayEvent] = []
        test_ranges = _resolve_bar_ranges(len(bars), config)
        total_steps = sum(end - start for start, end in test_ranges)
        step_counter = 0
        for range_start, range_end in test_ranges:
            for bar_index in range(max(range_start, _MIN_WARMUP_BARS), range_end):
                current_bar = bars[bar_index]
                score = self._score_bar(
                    asset=asset,
                    bar_index=bar_index,
                    bars=bars,
                    funding_bars=funding_bars,
                    btc_closes=btc_closes,
                    higher_bars=higher_bars,
                    config=config,
                )
                closed = tracker.update(
                    bar=current_bar,
                    current_bar_index=bar_index,
                    score=score,
                    thresholds_weak=config.thresholds.weak,
                )
                events.extend(_exit_events(closed, config.verbose_events))
                if (
                    score.total >= config.thresholds.weak
                    and score.direction != "NEUTRAL"
                ):
                    entry = tracker.try_open(score, current_bar, account_value, config.risk, bar_index)
                    if entry is not None and config.verbose_events:
                        events.append(_entry_event(entry, bar_index, score))
                elif config.verbose_events:
                    events.append(_skip_event(asset, bar_index, current_bar, score))
                account_value = _apply_pnl(account_value, closed)
                step_counter += 1
                if on_progress and step_counter % 50 == 0:
                    on_progress(
                        bars_complete_offset + step_counter,
                        total_bars_offset + total_steps,
                        asset,
                    )
        final_bar = bars[-1]
        forced = tracker.force_close_all(final_bar, len(bars) - 1)
        events.extend(_exit_events(forced, config.verbose_events))
        account_value = _apply_pnl(account_value, forced)
        return tracker.closed_trades, events, account_value

    def _score_bar(
        self,
        asset: str,
        bar_index: int,
        bars: list[OHLCVBar],
        funding_bars: list[FundingRateBar],
        btc_closes: list[Decimal],
        higher_bars: list[OHLCVBar],
        config: BacktestConfig,
    ) -> Any:
        """Build scorer input from bars visible at index T only."""
        lookback = bars[max(0, bar_index - 99):bar_index]
        funding_slice = _funding_visible_at(funding_bars, bars[bar_index].timestamp_utc)
        btc_slice = btc_closes[: bar_index + 1]
        higher_slice = _higher_tf_visible_at(higher_bars, bars[bar_index].timestamp_utc)
        scorer_input = BarScorerInput(
            asset=asset,
            bar_index=bar_index,
            current_bar=bars[bar_index],
            lookback_ohlcv=lookback,
            lookback_funding=funding_slice[-30:],
            btc_close_series=btc_slice,
            higher_tf_bars=higher_slice,
            live_signal_bonus=config.live_signal_bonus,
            thresholds=config.thresholds,
        )
        return self._scorer.score(scorer_input)


def compute_walk_forward_windows(
    total_bars: int,
    train_bars: int,
    test_bars: int,
) -> list[tuple[int, int, int, int]]:
    """Return train/test index ranges for overlapping walk-forward windows."""
    windows: list[tuple[int, int, int, int]] = []
    window_index = 0
    while True:
        train_start = window_index * test_bars
        train_end = train_start + train_bars
        test_start = train_end
        test_end = test_start + test_bars
        if test_end > total_bars:
            break
        windows.append((train_start, train_end, test_start, test_end))
        window_index += 1
    return windows


def _resolve_bar_ranges(total_bars: int, config: BacktestConfig) -> list[tuple[int, int]]:
    if not config.walk_forward:
        return [(0, total_bars)]
    windows = compute_walk_forward_windows(
        total_bars,
        config.walk_forward_train_bars,
        config.walk_forward_test_bars,
    )
    return [(test_start, test_end) for _, _, test_start, test_end in windows]


def _load_synthetic_bundle(asset: str, config: BacktestConfig) -> dict[str, Any]:
    generator = SyntheticDataGenerator()
    ohlcv = generator.generate_ohlcv(
        asset,
        n_bars=_SYNTHETIC_BAR_COUNT,
        timeframe=config.timeframe,
        seed=_SYNTHETIC_SEED,
    )
    funding = generator.generate_funding_rates(n_bars=_SYNTHETIC_BAR_COUNT, seed=_SYNTHETIC_SEED)
    btc_frame = generator.generate_ohlcv(
        "BTCUSDT",
        n_bars=_SYNTHETIC_BAR_COUNT,
        timeframe=config.timeframe,
        seed=_SYNTHETIC_SEED,
    )
    higher_tf = generator.generate_ohlcv(
        asset,
        n_bars=_SYNTHETIC_BAR_COUNT // 4,
        timeframe=config.higher_timeframe,
        seed=_SYNTHETIC_SEED,
    )
    btc_closes = [Decimal(str(value)) for value in btc_frame.get_column("close").to_list()]
    return {"ohlcv": ohlcv, "funding": funding, "btc_closes": btc_closes, "higher_tf": higher_tf}


def _load_db_bundle(
    db: BacktestDB | None,
    asset: str,
    config: BacktestConfig,
) -> dict[str, Any]:
    if db is None:
        db = BacktestDB.instance()
    ohlcv = db.get_ohlcv(asset, config.timeframe, config.start_date, config.end_date)
    funding = db.get_funding_rates(asset, config.start_date, config.end_date)
    btc_frame = db.get_ohlcv("BTCUSDT", config.timeframe, config.start_date, config.end_date)
    higher_tf = db.get_ohlcv(asset, config.higher_timeframe, config.start_date, config.end_date)
    btc_closes = [Decimal(str(value)) for value in btc_frame.get_column("close").to_list()]
    return {"ohlcv": ohlcv, "funding": funding, "btc_closes": btc_closes, "higher_tf": higher_tf}


def _dataframe_to_ohlcv_bars(frame: pl.DataFrame, asset: str, timeframe: str) -> list[OHLCVBar]:
    bars: list[OHLCVBar] = []
    for row in frame.iter_rows(named=True):
        bars.append(
            OHLCVBar(
                asset=asset,
                timestamp_utc=str(row["timestamp_utc"]),
                open=Decimal(str(row["open"])),
                high=Decimal(str(row["high"])),
                low=Decimal(str(row["low"])),
                close=Decimal(str(row["close"])),
                volume=Decimal(str(row["volume"])),
                volume_usd=Decimal(str(row["volume_usd"])),
                timeframe=timeframe,
            ),
        )
    return bars


def _dataframe_to_funding_bars(frame: pl.DataFrame, asset: str) -> list[FundingRateBar]:
    bars: list[FundingRateBar] = []
    for row in frame.iter_rows(named=True):
        bars.append(
            FundingRateBar(
                asset=asset,
                timestamp_utc=str(row["timestamp_utc"]),
                funding_rate=Decimal(str(row["funding_rate"])),
                funding_rate_annualised=Decimal(str(row["funding_annualised"])),
                open_interest_usd=(
                    Decimal(str(row["open_interest_usd"]))
                    if row.get("open_interest_usd") is not None
                    else None
                ),
            ),
        )
    return bars


def _funding_visible_at(funding_bars: list[FundingRateBar], timestamp: str) -> list[FundingRateBar]:
    return [bar for bar in funding_bars if bar.timestamp_utc <= timestamp]


def _higher_tf_visible_at(higher_bars: list[OHLCVBar], timestamp: str) -> list[OHLCVBar]:
    return [bar for bar in higher_bars if bar.timestamp_utc <= timestamp]


def _apply_pnl(account_value: Decimal, closed_trades: list[ClosedTrade]) -> Decimal:
    pnl = sum((trade.exit.pnl_usd for trade in closed_trades), Decimal("0"))
    return account_value + pnl


def _entry_event(entry: Any, bar_index: int, score: Any) -> ReplayEvent:
    return ReplayEvent(
        bar_index=bar_index,
        timestamp_utc=entry.entry_timestamp_utc,
        asset=entry.asset,
        event_type="ENTRY",
        score=score.total,
        signal_class=score.signal_class,
        message=f"Opened {entry.direction} at {entry.entry_price}",
        trade_id=entry.trade_id,
    )


def _skip_event(asset: str, bar_index: int, bar: OHLCVBar, score: Any) -> ReplayEvent:
    return ReplayEvent(
        bar_index=bar_index,
        timestamp_utc=bar.timestamp_utc,
        asset=asset,
        event_type="SKIP",
        score=score.total,
        signal_class=score.signal_class,
        message="Score below threshold or neutral direction",
        trade_id=None,
    )


def _exit_events(closed: list[ClosedTrade], verbose: bool) -> list[ReplayEvent]:
    if not verbose:
        return []
    return [
        ReplayEvent(
            bar_index=trade.exit.exit_bar_index,
            timestamp_utc=trade.exit.exit_timestamp_utc,
            asset=trade.entry.asset,
            event_type="EXIT",
            score=None,
            signal_class=trade.entry.signal_class,
            message=f"Closed {trade.exit.exit_reason} pnl={trade.exit.pnl_usd}",
            trade_id=trade.entry.trade_id,
        )
        for trade in closed
    ]
