"""Tests for execution score gating telemetry."""

from __future__ import annotations

from datetime import datetime, timezone

from atlas.pipeline.execution_gate import log_skipped_opportunity


def test_log_skipped_opportunity_formats_line() -> None:
    """Regression: skipped-opportunity logs stay structured and lightweight."""
    ts = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)
    log_skipped_opportunity(
        asset="BTCUSDT",
        timestamp=ts,
        score=42,
        reason="unit_test",
        min_trade_score=68,
        raw_confluence_score=90,
        extra={"cycle": "x1"},
    )
