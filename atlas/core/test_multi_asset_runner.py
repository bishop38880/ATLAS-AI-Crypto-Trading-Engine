"""Tests for MultiAssetRunner."""

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from atlas.core.multi_asset_runner import MultiAssetRunner
from atlas.models.signal import (
    ActionBlock,
    CategoryScores,
    SignalDecision,
    SignalOutput,
)
from atlas.models.telemetry import TelemetryEvent


@pytest.fixture
def mock_redis() -> AsyncMock:
    return AsyncMock()


def _make_test_signal(
    sig_id: str, asset: str, price: Decimal,
    sl: Decimal, tp: Decimal, score: int,
) -> SignalOutput:
    """Build a test SignalOutput with standard defaults."""
    base_time = datetime.now(timezone.utc)
    return SignalOutput(
        signal_id=sig_id, timestamp=base_time,
        decision=SignalDecision.BUY, asset=asset,
        action=ActionBlock(
            side="buy", order_type="limit",
            price=price, stop_loss=sl, take_profit=tp,
        ),
        expires_at=base_time + timedelta(hours=1),
        score=score, confidence=Decimal(str(score / 100.0)),
        category_scores=CategoryScores(total=score, correlation=score),
        telemetry=TelemetryEvent(cycle_id="test", cycle_latency_ms=10.0, agent_count=1),
    )


@pytest.fixture
def sample_signals() -> list[SignalOutput]:
    return [
        _make_test_signal("sig-alt-1", "ADA/USDT", Decimal("1.0"), Decimal("0.9"), Decimal("1.2"), 60),
        _make_test_signal("sig-alt-2", "BCH/USDT", Decimal("500"), Decimal("490"), Decimal("520"), 90),
        _make_test_signal("sig-alt-3", "SOL/USDT", Decimal("50"), Decimal("49"), Decimal("52"), 70),
    ]


def test_apply_portfolio_filter(
    mock_redis: AsyncMock, sample_signals: list[SignalOutput],
) -> None:
    """Test that herding risk suppresses lower conviction signals."""
    runner = MultiAssetRunner(mock_redis)
    filtered = runner._apply_portfolio_filter(sample_signals)

    # We expect 1 signal from the 'alt' group (highest score = 90 = BCH)
    assert len(filtered) == 1
    assert filtered[0].asset == "BCH/USDT"


def test_apply_portfolio_filter_different_decisions(
    mock_redis: AsyncMock, sample_signals: list[SignalOutput],
) -> None:
    """Signals with different decisions shouldn't be suppressed together."""
    runner = MultiAssetRunner(mock_redis)
    # Change one signal to SELL
    sample_signals[0] = sample_signals[0].model_copy(
        update={"decision": SignalDecision.SELL},
    )

    filtered = runner._apply_portfolio_filter(sample_signals)

    # Expect the SELL signal and the highest BUY signal
    assert len(filtered) == 2
    assets = [f.asset for f in filtered]
    assert "ADA/USDT" in assets
    assert "BCH/USDT" in assets


@pytest.mark.asyncio
async def test_runner_start_close(mock_redis: AsyncMock) -> None:
    """Test start and close lifecycle."""
    runner = MultiAssetRunner(mock_redis)
    await runner.start()
    assert runner._running is True
    assert runner._task is not None

    await runner.close()
    assert runner._running is False
    assert runner._task.cancelled() or runner._task.done()
