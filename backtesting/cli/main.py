"""CLI entry point and subcommand router."""

from __future__ import annotations

import argparse
import asyncio
import sys

from backtesting.cli.commands import data as data_command
from backtesting.cli.commands import results as results_command
from backtesting.cli.commands import run as run_command
from backtesting.cli.commands import tutorial as tutorial_command
from backtesting.cli import wizard as wizard_module


def build_root_parser() -> argparse.ArgumentParser:
    """Build the top-level argparse parser with subcommands."""
    parser = argparse.ArgumentParser(
        prog="backtesting",
        description="POLARIS offline backtesting suite",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")
    run_command.register_parser(subparsers)
    data_command.register_parser(subparsers)
    results_command.register_parser(subparsers)
    tutorial_command.register_parser(subparsers)
    subparsers.add_parser("wizard", help="Interactive configuration wizard")
    return parser


def main(argv: list[str] | None = None) -> None:
    """Parse arguments and dispatch to the selected subcommand."""
    parser = build_root_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return
    try:
        _dispatch(args)
    except KeyboardInterrupt:
        sys.exit(130)
    except ValueError as exc:
        parser.error(str(exc))


def _dispatch(args: argparse.Namespace) -> None:
    if args.command == "run":
        asyncio.run(run_command.execute(args))
        return
    if args.command == "data":
        asyncio.run(data_command.execute(args))
        return
    if args.command == "results":
        results_command.execute(args)
        return
    if args.command == "tutorial":
        tutorial_command.execute(args)
        return
    if args.command == "wizard":
        asyncio.run(wizard_module.execute())
        return
    raise ValueError(f"Unknown command: {args.command}")
