"""Tests for backtesting CLI (BT-04)."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

import pytest
from rich.console import Console

from backtesting.analytics.metrics import MonthlyReturns
from backtesting.cli import tutorial_chapters
from backtesting.cli.display.charts import render_equity_curve, render_monthly_heatmap
from backtesting.cli.display.summary import verdict_style
from backtesting.cli.runner import parse_threshold_triplet
from backtesting.cli.tutorial_state import TutorialProgress, load_progress, save_progress
from backtesting.cli.wizard import _collect_config
from backtesting.engine.config import BacktestConfig, RiskConfig


class TestWizardConfig:
    """Wizard builds valid BacktestConfig from mock answers."""

    def test_wizard_builds_valid_config(self) -> None:
        answers = iter(["2", "2024-01-01", "2024-06-01", "10000"])
        confirms = iter([True, False, False])

        with patch("backtesting.cli.wizard.Prompt.ask", side_effect=lambda *a, **k: next(answers)):
            with patch("backtesting.cli.wizard.Confirm.ask", side_effect=lambda *a, **k: next(confirms)):
                config = _collect_config()
        assert config.assets == [
            "BTCUSDT",
            "ETHUSDT",
            "SOLUSDT",
            "BNBUSDT",
            "XRPUSDT",
            "AVAXUSDT",
        ]
        assert config.risk.account_size_usd == Decimal("10000")


class TestAsciiCharts:
    """ASCII chart helpers render without error."""

    def test_flat_equity_curve_renders(self) -> None:
        curve = [
            {"bar_index": 1, "equity_usd": "10000", "drawdown_pct": "0", "drawdown_usd": "0"},
            {"bar_index": 2, "equity_usd": "10000", "drawdown_pct": "0", "drawdown_usd": "0"},
        ]
        output = render_equity_curve(curve)
        assert "$10,000" in output

    def test_drawdown_equity_curve_renders(self) -> None:
        curve = [
            {"bar_index": 1, "equity_usd": "10000", "drawdown_pct": "0", "drawdown_usd": "0"},
            {"bar_index": 2, "equity_usd": "9000", "drawdown_pct": "10", "drawdown_usd": "1000"},
            {"bar_index": 3, "equity_usd": "9500", "drawdown_pct": "5", "drawdown_usd": "500"},
        ]
        output = render_equity_curve(curve)
        assert "┤" in output

    def test_monthly_heatmap_includes_twelve_month_columns(self) -> None:
        rows = [
            MonthlyReturns(year=2024, month=month, pnl_usd=Decimal("10"), pnl_pct=Decimal("1"), trade_count=1, win_count=1)
            for month in range(1, 13)
        ]
        output = render_monthly_heatmap(rows)
        assert "Jan" not in output
        assert "2024" in output
        for month in range(1, 13):
            assert f"{month:>7}" in output or "+1.0%" in output


class TestVerdictStyles:
    """Verdict banner colour mapping."""

    @pytest.mark.parametrize(
        ("verdict", "expected_fragment"),
        [
            ("STRONG_EDGE", "bright_green"),
            ("PROMISING", "green"),
            ("MARGINAL", "yellow"),
            ("NO_EDGE", "red"),
            ("DEGRADED", "bright_red"),
        ],
    )
    def test_verdict_style_mapping(self, verdict: str, expected_fragment: str) -> None:
        assert expected_fragment in verdict_style(verdict)


class TestTutorialIntegration:
    """Tutorial chapters run offline without user input."""

    def test_chapter_1_runs_non_interactive(self) -> None:
        tutorial_chapters.chapter_1_confluence_scoring(interactive=False)

    def test_chapter_4_runs_synthetic_backtest(self) -> None:
        config = asyncio.run(tutorial_chapters.chapter_4_first_backtest(interactive=False))
        assert config.use_synthetic is True
        assert config.assets == ["BTCUSDT"]


class TestThresholdParsing:
    """CLI threshold parser."""

    def test_default_thresholds(self) -> None:
        assert parse_threshold_triplet(None) == (120, 150, 180)

    def test_custom_thresholds(self) -> None:
        assert parse_threshold_triplet("130,160,190") == (130, 160, 190)


class TestTutorialProgress:
    """Tutorial progress persistence."""

    def test_save_and_load_progress(self, tmp_path, monkeypatch) -> None:
        path = tmp_path / "progress.json"
        monkeypatch.setattr("backtesting.cli.tutorial_state._PROGRESS_PATH", path)
        save_progress(TutorialProgress(last_chapter=2, completed_chapters=[1, 2]))
        loaded = load_progress()
        assert loaded.last_chapter == 2
        assert loaded.completed_chapters == [1, 2]
