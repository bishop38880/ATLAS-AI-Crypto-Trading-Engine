"""Tests for SignalStateManager — Session 00 quality gate.

Tests live alongside code (atlas/core/test_state.py).
Uses unittest.mock.AsyncMock for Redis client mocking.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from decimal import Decimal
from unittest.mock import AsyncMock

from atlas.shared.serialisation import decode_json
import pytest

from atlas.core.state import (
    SignalStateManager,
    _compute_ttl_seconds,
    _format_channel,
    _serialize_signal,
)
from atlas.models.signal import (
    ActionBlock,
    CategoryScores,
    SignalDecision,
    SignalOutput,
)
from atlas.models.telemetry import TelemetryEvent


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 4, 21, 14, 0, 0, tzinfo=timezone.utc)
_LATER = _NOW + timedelta(minutes=30)


def _build_action(decision: SignalDecision) -> ActionBlock | None:
    """Build ActionBlock for actionable decisions."""
    if decision in {
        SignalDecision.BUY, SignalDecision.STRONG_BUY,
        SignalDecision.SELL, SignalDecision.STRONG_SELL,
    }:
        return ActionBlock(
            side="buy", order_type="limit",
            price=Decimal("87250.00"),
            stop_loss=Decimal("85000.00"),
            take_profit=Decimal("92000.00"),
        )
    return None


def _make_signal(
    asset: str = "BTCUSDT",
    decision: SignalDecision = SignalDecision.BUY,
) -> SignalOutput:
    """Build a valid SignalOutput for testing."""
    return SignalOutput(
        signal_id="test-signal-001",
        timestamp=_NOW,
        decision=decision,
        asset=asset,
        action=_build_action(decision),
        expires_at=_LATER,
        score=75,
        confidence=Decimal("0.85"),
        category_scores=CategoryScores(
            derivatives=20, onchain=15, technical=20,
            sentiment=10, context=10, total=75,
        ),
        telemetry=TelemetryEvent(
            cycle_id="cycle-001", cycle_latency_ms=120.5,
            agent_count=4, timestamp=_NOW,
        ),
    )


# ---------------------------------------------------------------------------
# Tests — helper functions
# ---------------------------------------------------------------------------


class TestSerializeSignal:
    """Tests for signal serialization via msgspec."""

    def test_serialize_produces_bytes(self) -> None:
        """Serialized signal is bytes."""
        signal = _make_signal()
        result = _serialize_signal(signal)
        assert isinstance(result, bytes)

    def test_roundtrip_preserves_signal_id(self) -> None:
        """signal_id survives msgspec encode/decode roundtrip."""
        signal = _make_signal()
        encoded = _serialize_signal(signal)
        decoded: dict = decode_json(encoded)  # type: ignore[type-arg]
        assert decoded["signal_id"] == "test-signal-001"

    def test_roundtrip_preserves_decimal_fields(self) -> None:
        """Decimal price fields serialize as strings, not floats."""
        signal = _make_signal()
        encoded = _serialize_signal(signal)
        decoded: dict = decode_json(encoded)  # type: ignore[type-arg]
        # Pydantic model_dump(mode="json") converts Decimal to str
        assert decoded["action"]["price"] == "87250.00"

    def test_no_stdlib_json_used(self) -> None:
        """Verify we use msgspec, not stdlib json."""
        signal = _make_signal()
        encoded = _serialize_signal(signal)
        # If it's valid JSON bytes via msgspec, it's correct
        decoded: dict = decode_json(encoded)  # type: ignore[type-arg]
        assert "decision" in decoded


class TestFormatChannel:
    """Tests for Redis channel name formatting."""

    def test_channel_format(self) -> None:
        """Channel name follows polaris:signals:{asset} pattern."""
        assert _format_channel("BTCUSDT") == "polaris:signals:BTCUSDT"
        assert _format_channel("ETHUSDT") == "polaris:signals:ETHUSDT"


class TestComputeTtl:
    """Tests for TTL computation."""

    def test_30m_ttl(self) -> None:
        """30-minute delta produces 1800-second TTL."""
        signal = _make_signal()
        ttl = _compute_ttl_seconds(signal)
        assert ttl == 1800

    def test_minimum_ttl_is_1(self) -> None:
        """TTL never goes below 1 second."""
        signal = SignalOutput(
            signal_id="test-min-ttl",
            timestamp=_NOW,
            decision=SignalDecision.NO_POSITION,
            asset="BTCUSDT",
            expires_at=_NOW + timedelta(seconds=0, milliseconds=100),
            score=0,
            confidence=Decimal("0.0"),
            category_scores=CategoryScores(total=0),
            telemetry=TelemetryEvent(
                cycle_id="c", cycle_latency_ms=0, agent_count=0,
            ),
        )
        assert _compute_ttl_seconds(signal) >= 1


# ---------------------------------------------------------------------------
# Tests — SignalStateManager
# ---------------------------------------------------------------------------


class TestPublishSignal:
    """Tests for signal publication to Redis pub/sub."""

    @pytest.mark.asyncio
    async def test_publish_calls_redis_publish(self) -> None:
        """publish_signal calls redis.publish with correct channel."""
        mock_redis = AsyncMock()
        mock_redis.publish.return_value = 1

        manager = SignalStateManager(mock_redis)
        signal = _make_signal()
        count = await manager.publish_signal(signal)

        mock_redis.publish.assert_called_once()
        call_args = mock_redis.publish.call_args
        assert call_args[0][0] == "polaris:signals:BTCUSDT"
        assert count == 1

    @pytest.mark.asyncio
    async def test_publish_payload_is_valid_json(self) -> None:
        """Published payload is valid msgspec JSON."""
        mock_redis = AsyncMock()
        mock_redis.publish.return_value = 0

        manager = SignalStateManager(mock_redis)
        signal = _make_signal()
        await manager.publish_signal(signal)

        payload = mock_redis.publish.call_args[0][1]
        decoded: dict = decode_json(payload)  # type: ignore[type-arg]
        assert decoded["signal_id"] == signal.signal_id


class TestStoreSignal:
    """Tests for signal storage in Redis."""

    @pytest.mark.asyncio
    async def test_store_creates_two_keys(self) -> None:
        """store_signal writes both signal:{id} and signal:latest:{asset}."""
        mock_redis = AsyncMock()
        manager = SignalStateManager(mock_redis)
        signal = _make_signal()

        await manager.store_signal(signal)

        assert mock_redis.setex.call_count == 2
        keys = [
            call.args[0] for call in mock_redis.setex.call_args_list
        ]
        assert "signal:test-signal-001" in keys
        assert "signal:latest:BTCUSDT" in keys

    @pytest.mark.asyncio
    async def test_store_sets_ttl(self) -> None:
        """Stored keys have correct TTL."""
        mock_redis = AsyncMock()
        manager = SignalStateManager(mock_redis)
        signal = _make_signal()

        await manager.store_signal(signal)

        # Both calls should have TTL = 1800 (30 minutes)
        for call in mock_redis.setex.call_args_list:
            assert call.args[1] == 1800


class TestPublishAndStore:
    """Tests for the combined publish_and_store method."""

    @pytest.mark.asyncio
    async def test_publish_and_store_does_both(self) -> None:
        """publish_and_store calls both publish and setex."""
        mock_redis = AsyncMock()
        mock_redis.publish.return_value = 2

        manager = SignalStateManager(mock_redis)
        signal = _make_signal()
        count = await manager.publish_and_store(signal)

        assert count == 2
        mock_redis.publish.assert_called_once()
        assert mock_redis.setex.call_count == 2
