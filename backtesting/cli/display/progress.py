"""Rich progress helpers for backtest replay."""

from __future__ import annotations

from collections.abc import Callable

from rich.console import Console
from rich.progress import BarColumn, Progress, TaskProgressColumn, TextColumn


def build_replay_progress(console: Console) -> Progress:
    """Create a Rich progress instance for bar replay."""
    return Progress(
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TextColumn("{task.fields[trade_detail]}"),
        console=console,
    )


def make_progress_callback(
    progress: Progress,
    task_id: int,
    trade_counter: list[int],
) -> Callable[[int, int, str], None]:
    """Return a callback that updates the Rich progress bar."""

    def on_progress(completed: int, total: int, asset: str) -> None:
        if total <= 0:
            return
        progress.update(
            task_id,
            completed=completed,
            total=total,
            description=f"Replaying {asset}",
            trade_detail=f"{completed:,}/{total:,} bars · {trade_counter[0]} trades",
        )

    return on_progress
