"""Tutorial chapter content for the POLARIS backtesting walkthrough."""

from __future__ import annotations

from decimal import Decimal

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from backtesting.analytics.metrics import compute_all_metrics
from backtesting.cli.commands.data import print_coverage_status
from backtesting.cli.display.charts import render_score_bar
from backtesting.cli.display.progress import build_replay_progress, make_progress_callback
from backtesting.cli.display.summary import render_compact_summary, render_full_result
from backtesting.cli.runner import run_backtest_pipeline
from backtesting.data.synthetic import SyntheticDataGenerator
from backtesting.engine.config import BacktestConfig, ScoreThresholds
from backtesting.engine.position import ClosedTrade
from backtesting.engine.scorer import BarScorer, BarScorerInput
from backtesting.data.models import FundingRateBar, OHLCVBar

console = Console()


def chapter_1_confluence_scoring(*, interactive: bool) -> None:
    """Explain 220-point scoring with a sample breakdown."""
    console.print(Panel(
        "POLARIS uses a 220-point confluence score across derivatives, on-chain, "
        "sentiment, technical, and market-context dimensions.",
        title="Chapter 1: What is Confluence Scoring?",
    ))
    table = Table(title="Example signal: SOLUSDT LONG")
    table.add_column("Dimension")
    table.add_column("Pts", justify="right")
    table.add_column("Max", justify="right")
    table.add_column("Bar")
    rows = [
        ("Derivatives", 58, 75),
        ("On-Chain / Whale", 41, 65),
        ("Social Sentiment", 8, 35),
        ("Technical Structure", 12, 15),
        ("Market Context", 24, 30),
    ]
    for name, pts, maximum in rows:
        table.add_row(name, str(pts), str(maximum), render_score_bar(pts, maximum))
    table.add_row("TOTAL", "143", "220", "→ WEAK SIGNAL")
    console.print(table)
    _run_quiz(interactive)


def chapter_2_data(*, interactive: bool) -> None:
    """Show data coverage and synthetic sample rows."""
    console.print(Panel("Chapter 2: Understanding the Data", border_style="cyan"))
    print_coverage_status()
    frame = SyntheticDataGenerator().generate_ohlcv("BTCUSDT", n_bars=5, seed=7)
    table = Table(title="Sample BTCUSDT 1h bars (synthetic)")
    table.add_column("Timestamp")
    for column in ("open", "high", "low", "close", "volume"):
        table.add_column(column.title(), justify="right")
    for row in frame.iter_rows(named=True):
        table.add_row(
            str(row["timestamp_utc"])[:16],
            f"{float(row['open']):,.0f}",
            f"{float(row['high']):,.0f}",
            f"{float(row['low']):,.0f}",
            f"{float(row['close']):,.0f}",
            f"{float(row['volume']):,.0f}",
        )
    console.print(table)
    _pause(interactive)


def chapter_3_signal_generation(*, interactive: bool) -> None:
    """Walk through bar scoring step by step."""
    console.print(Panel("Chapter 3: How Signals Are Generated", border_style="cyan"))
    bars = _build_tutorial_bars()
    funding = _build_tutorial_funding(len(bars))
    scorer = BarScorer()
    bar_index = min(120, len(bars) - 1)
    scorer_input = BarScorerInput(
        asset="BTCUSDT",
        bar_index=bar_index,
        current_bar=bars[bar_index],
        lookback_ohlcv=bars[max(0, bar_index - 99):bar_index],
        lookback_funding=funding[: bar_index + 1][-30:],
        btc_close_series=[bar.close for bar in bars[: bar_index + 1]],
        higher_tf_bars=bars[: bar_index + 1:4],
    )
    score = scorer.score(scorer_input)
    console.print(
        f"Bar: {bars[bar_index].timestamp_utc[:16]} UTC  |  BTCUSDT  |  "
        f"Close: ${bars[bar_index].close:,.0f}",
    )
    console.print("Step 1: Funding + volume derivatives scoring applied.")
    console.print("Step 2: RSI, ADX, Bollinger, MACD technical scoring applied.")
    console.print("Step 3: BTC correlation and higher-TF market context applied.")
    console.print(f"TOTAL SCORE: {score.total} → {score.signal_class} ({score.direction})")
    _pause(interactive)


async def chapter_4_first_backtest(*, interactive: bool) -> BacktestConfig:
    """Run a minimal synthetic backtest."""
    console.print(Panel("Chapter 4: Running Your First Backtest", border_style="cyan"))
    config = BacktestConfig(
        assets=["BTCUSDT"],
        start_date="2024-01-01",
        end_date="2024-04-01",
        use_synthetic=True,
        description="tutorial-chapter-4",
    )
    progress = build_replay_progress(console)
    task_id = progress.add_task("Replaying BTCUSDT", total=100, trade_detail="")
    trade_counter = [0]
    with progress:
        result = await run_backtest_pipeline(
            config,
            on_progress=make_progress_callback(progress, task_id, trade_counter),
            save_to_db=False,
        )
    render_compact_summary(result, console)
    _pause(interactive)
    return config


async def chapter_5_reading_results(config: BacktestConfig, *, interactive: bool) -> object:
    """Explain each analytics panel."""
    console.print(Panel("Chapter 5: Reading the Results", border_style="cyan"))
    result = await run_backtest_pipeline(config, save_to_db=False)
    render_full_result(result, console)
    console.print(
        "Higher conviction buckets should outperform weaker buckets when scoring is calibrated.",
    )
    _pause(interactive)
    return result


async def chapter_6_walk_forward(config: BacktestConfig, *, interactive: bool) -> None:
    """Compare standard vs walk-forward returns."""
    console.print(Panel("Chapter 6: Walk-Forward Analysis", border_style="cyan"))
    standard = await run_backtest_pipeline(config, save_to_db=False)
    walk_config = config.model_copy(update={"walk_forward": True})
    walk_result = await run_backtest_pipeline(walk_config, save_to_db=False)
    console.print(f"Standard backtest return:    {standard.metrics.total_pnl_pct:+.1f}%")
    console.print(f"Walk-forward return:         {walk_result.metrics.total_pnl_pct:+.1f}%")
    _pause(interactive)


async def chapter_7_calibration(
    trades: list[ClosedTrade],
    config: BacktestConfig,
    *,
    interactive: bool,
) -> None:
    """Interactive threshold comparison."""
    console.print(Panel("Chapter 7: Calibrating Your Thresholds", border_style="cyan"))
    baseline = _metrics_for_threshold(trades, config, config.thresholds.weak)
    raised = _metrics_for_threshold(trades, config, 130)
    table = Table(title="Threshold comparison")
    table.add_column("")
    table.add_column("Threshold ≥120", justify="right")
    table.add_column("Threshold ≥130", justify="right")
    for label, left, right in (
        ("Total Trades", baseline.total_trades, raised.total_trades),
        ("Win Rate", f"{baseline.win_rate_pct:.1f}%", f"{raised.win_rate_pct:.1f}%"),
        ("Profit Factor", f"{baseline.profit_factor:.2f}", f"{raised.profit_factor:.2f}"),
        ("Total Return", f"{baseline.total_pnl_pct:+.1f}%", f"{raised.total_pnl_pct:+.1f}%"),
    ):
        table.add_row(label, str(left), str(right))
    console.print(table)
    console.print(
        "\n[green]What to do next:[/] ingest real data, run paper trading, then calibrate thresholds.",
    )
    _pause(interactive)


def _run_quiz(interactive: bool) -> None:
    if not interactive:
        console.print("[dim]Quiz answer: (b) BUY — score 165 is between 150 and 179.[/]")
        return
    answer = Prompt.ask(
        "A score of 165 would be classified as: (a) STRONG (b) BUY (c) WEAK (d) NO_TRADE",
        choices=["a", "b", "c", "d"],
        default="b",
    )
    if answer == "b":
        console.print("[green]Correct — 165 is a BUY signal (≥150, <180).[/]")
        return
    console.print("[yellow]Not quite — 165 falls in the BUY band (150–179).[/]")


def _pause(interactive: bool) -> None:
    if interactive:
        Prompt.ask("Press Enter to continue", default="")


def _build_tutorial_bars() -> list[OHLCVBar]:
    frame = SyntheticDataGenerator().generate_ohlcv("BTCUSDT", n_bars=200, seed=11)
    bars: list[OHLCVBar] = []
    for row in frame.iter_rows(named=True):
        bars.append(
            OHLCVBar(
                asset="BTCUSDT",
                timestamp_utc=str(row["timestamp_utc"]),
                open=Decimal(str(row["open"])),
                high=Decimal(str(row["high"])),
                low=Decimal(str(row["low"])),
                close=Decimal(str(row["close"])),
                volume=Decimal(str(row["volume"])),
                volume_usd=Decimal(str(row["volume_usd"])),
                timeframe="1h",
            ),
        )
    return bars


def _build_tutorial_funding(count: int) -> list[FundingRateBar]:
    frame = SyntheticDataGenerator().generate_funding_rates(n_bars=count, seed=11)
    return [
        FundingRateBar(
            asset="BTCUSDT",
            timestamp_utc=str(row["timestamp_utc"]),
            funding_rate=Decimal(str(row["funding_rate"])),
            funding_rate_annualised=Decimal(str(row["funding_annualised"])),
        )
        for row in frame.iter_rows(named=True)
    ]


def _metrics_for_threshold(
    trades: list[ClosedTrade],
    config: BacktestConfig,
    minimum_score: int,
):
    filtered = [trade for trade in trades if trade.entry.score_at_entry >= minimum_score]
    return compute_all_metrics(filtered, config.risk.account_size_usd, 1400)
