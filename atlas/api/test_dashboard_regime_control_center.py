"""Tests for GET /api/dashboard/regime-control-center payload assembly."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import msgspec
import pytest

from atlas.api.dashboard_regime_payload import build_regime_control_center_payload
from atlas.core.regime_snapshots import POLARIS_REGIME_HISTORY_KEY
from atlas.shared.config import PolarisSettings


UTC = timezone.utc


def _btc_signal_bull() -> dict:
    return {
        "asset": "BTCUSDT",
        "timestamp": datetime.now(tz=UTC).isoformat(),
        "confidence": 0.72,
        "confidence_tier": "STANDARD",
        "position_size_modifier": 1.0,
        "gate_threshold": 140,
        "agent_breakdown": {
            "regime": {
                "agent_name": "regime",
                "explanation": "Regime is BULL (duration: 12 bars)",
                "sub_signals": {
                    "regime": "bull",
                    "probabilities": {"bull": 0.7, "bear": 0.1, "volatile": 0.2},
                    "duration": 12,
                    "transition_prob": 0.08,
                },
            }
        },
    }


@pytest.mark.asyncio
async def test_regime_control_center_resolves_hmm_and_adjustments() -> None:
    import fakeredis.aioredis

    redis = fakeredis.aioredis.FakeRedis(decode_responses=False)
    raw = msgspec.json.encode(_btc_signal_bull())
    await redis.set("polaris:signals:BTCUSDT", raw)

    settings = PolarisSettings()
    payload = await build_regime_control_center_payload(redis, settings, focus_asset="BTC")

    assert payload.hmm_regime == "bull"
    assert payload.dashboard_regime == "BULL"
    assert payload.gate_threshold == 140
    assert payload.position_size_modifier == 1.0
    assert "bull" in payload.regime_probabilities


@pytest.mark.asyncio
async def test_regime_timeline_counts_switches() -> None:
    import fakeredis.aioredis

    redis = fakeredis.aioredis.FakeRedis(decode_responses=False)
    await redis.set("polaris:signals:BTCUSDT", msgspec.json.encode(_btc_signal_bull()))

    now = datetime.now(tz=UTC)
    e1 = {
        "ts": (now - timedelta(days=2)).isoformat(),
        "asset": "BTCUSDT",
        "hmm_regime": "bear",
        "dashboard_regime": "BEAR",
        "btc_price": "98000",
    }
    e2 = {
        "ts": (now - timedelta(days=1)).isoformat(),
        "asset": "BTCUSDT",
        "hmm_regime": "bull",
        "dashboard_regime": "BULL",
        "btc_price": "100000",
    }
    await redis.lpush(POLARIS_REGIME_HISTORY_KEY, msgspec.json.encode(e2))
    await redis.lpush(POLARIS_REGIME_HISTORY_KEY, msgspec.json.encode(e1))

    settings = PolarisSettings()
    payload = await build_regime_control_center_payload(redis, settings, focus_asset="BTC")

    assert payload.regime_switches_7d >= 1
    assert len(payload.timeline) >= 1
