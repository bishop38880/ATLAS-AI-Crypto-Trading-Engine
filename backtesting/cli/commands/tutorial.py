"""`backtesting tutorial` interactive walkthrough."""

from __future__ import annotations

import argparse
import asyncio

from rich.console import Console
from rich.prompt import Prompt

from backtesting.cli import tutorial_chapters as chapters
from backtesting.cli.tutorial_state import load_progress, mark_chapter_complete
from backtesting.engine.config import BacktestConfig

console = Console()

_CHAPTER_RUNNERS = {
    1: chapters.chapter_1_confluence_scoring,
    2: chapters.chapter_2_data,
    3: chapters.chapter_3_signal_generation,
}


def register_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the `tutorial` subcommand."""
    parser = subparsers.add_parser("tutorial", help="Interactive POLARIS backtesting tutorial")
    parser.add_argument("--chapter", type=int, default=None, help="Jump to chapter 1-7")
    parser.add_argument("--no-interactive", action="store_true", help="Skip prompts (for CI)")


def execute(args: argparse.Namespace) -> None:
    """Run the tutorial from the selected chapter."""
    asyncio.run(_run_tutorial(args))


async def _run_tutorial(args: argparse.Namespace) -> None:
    interactive = not args.no_interactive
    progress = load_progress()
    console.print("\n[bold]POLARIS Backtesting Tutorial[/]")
    console.print("═════════════════════════════")
    for index in range(1, 8):
        console.print(f"Chapter {index} of 7: {_chapter_title(index)}")
    start_chapter = args.chapter or _resolve_start_chapter(interactive, progress.last_chapter)
    await _run_from_chapter(start_chapter, interactive=interactive)


def _resolve_start_chapter(interactive: bool, last_chapter: int) -> int:
    if not interactive:
        return 1
    raw = Prompt.ask(
        "Press Enter to start Chapter 1, or type a chapter number to jump",
        default=str(max(last_chapter + 1, 1)),
    )
    if not raw.strip():
        return max(last_chapter + 1, 1)
    return int(raw.strip())


async def _run_from_chapter(start: int, *, interactive: bool) -> None:
    config = _default_tutorial_config() if start >= 4 else None
    analytics_result = None
    for chapter in range(start, 8):
        if chapter <= 3:
            runner = _CHAPTER_RUNNERS[chapter]
            runner(interactive=interactive)
        elif chapter == 4:
            config = await chapters.chapter_4_first_backtest(interactive=interactive)
        elif chapter == 5 and config is not None:
            analytics_result = await chapters.chapter_5_reading_results(config, interactive=interactive)
        elif chapter == 6 and config is not None:
            await chapters.chapter_6_walk_forward(config, interactive=interactive)
        elif chapter == 7 and config is not None and analytics_result is not None:
            await chapters.chapter_7_calibration(
                analytics_result.trades,
                config,
                interactive=interactive,
            )
        mark_chapter_complete(chapter)


def _default_tutorial_config() -> BacktestConfig:
    return BacktestConfig(
        assets=["BTCUSDT"],
        start_date="2024-01-01",
        end_date="2024-04-01",
        use_synthetic=True,
        description="tutorial",
    )


def _chapter_title(chapter: int) -> str:
    titles = {
        1: "What is Confluence Scoring?",
        2: "Understanding the Data",
        3: "How Signals Are Generated",
        4: "Running Your First Backtest",
        5: "Reading the Results",
        6: "Walk-Forward Analysis",
        7: "Calibrating Your Thresholds",
    }
    return titles.get(chapter, "Unknown")
