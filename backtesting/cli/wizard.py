"""Interactive backtest configuration wizard."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from rich.console import Console
from rich.prompt import Confirm, Prompt

from backtesting.cli.display.summary import render_config_panel, render_full_result
from backtesting.cli.runner import run_backtest_pipeline
from backtesting.data.constants import POLARIS_UNIVERSE_ASSETS
from backtesting.engine.config import BacktestConfig, RiskConfig, ScoreThresholds

console = Console()

_TIER_ONE_ASSETS = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    "XRPUSDT",
    "AVAXUSDT",
]


async def execute() -> None:
    """Run the interactive wizard and optionally start a backtest."""
    console.print("\n[bold cyan]POLARIS Backtesting Wizard[/]")
    console.print("──────────────────────────")
    config = _collect_config()
    render_config_panel(config, console)
    if not Confirm.ask("Ready to run?", default=True):
        if Confirm.ask("Save configuration to file?", default=False):
            path = Prompt.ask("File path", default="backtest_config.json")
            _save_config_stub(config, path)
        return
    result = await run_backtest_pipeline(config, monte_carlo=config.walk_forward)
    render_full_result(result, console)


def _collect_config() -> BacktestConfig:
    assets = _ask_assets()
    start_date = Prompt.ask("Start date (YYYY-MM-DD)", default="2024-01-01")
    end_date = Prompt.ask("End date (YYYY-MM-DD)", default=date.today().isoformat())
    account_raw = Prompt.ask("Initial account size in USD", default="10000")
    walk_forward = Confirm.ask("Enable walk-forward analysis?", default=True)
    monte_carlo = Confirm.ask("Run Monte Carlo simulation?", default=True)
    use_synthetic = Confirm.ask("Use synthetic offline data?", default=False)
    return BacktestConfig(
        assets=assets,
        start_date=start_date,
        end_date=end_date,
        walk_forward=walk_forward,
        use_synthetic=use_synthetic,
        risk=RiskConfig(account_size_usd=Decimal(account_raw)),
        thresholds=ScoreThresholds(),
        description="wizard-run",
    )


def _ask_assets() -> list[str]:
    console.print("? Which assets would you like to test?")
    console.print("  > 1. Full 33-asset universe")
    console.print("    2. Tier 1 only (BTC, ETH, SOL, BNB, XRP, AVAX)")
    console.print("    3. Enter custom list")
    choice = Prompt.ask("Choice", choices=["1", "2", "3"], default="2")
    if choice == "1":
        return list(POLARIS_UNIVERSE_ASSETS)
    if choice == "2":
        return list(_TIER_ONE_ASSETS)
    raw = Prompt.ask("Comma-separated symbols")
    return [item.strip().upper() for item in raw.split(",") if item.strip()]


def _save_config_stub(config: BacktestConfig, path: str) -> None:
    import msgspec

    encoded = msgspec.json.encode(config.model_dump(mode="json"))
    from pathlib import Path

    Path(path).write_bytes(encoded)
    console.print(f"[green]Saved configuration to {path}[/]")
