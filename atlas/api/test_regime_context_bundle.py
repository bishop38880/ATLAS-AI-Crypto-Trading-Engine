"""Tests for /ws/system regime context enrichment from cached signals."""

from __future__ import annotations

import pytest

from atlas.api._channel_reads import _regime_context_bundle


def test_regime_context_bundle_extracts_asset_timeframe_and_runner_up() -> None:
    """Wire fields are populated from agent_breakdown when present."""
    sig = {
        "asset": "BTCUSDT",
        "timeframe": "4h",
        "timestamp": "2026-05-15T12:00:00+00:00",
        "confidence": 0.42,
        "agent_breakdown": {
            "regime": {
                "agent_name": "regime",
                "explanation": "Regime is VOLATILE (duration: 7 bars)",
                "sub_signals": {
                    "regime": "volatile",
                    "duration": 7,
                    "probabilities": {"volatile": 0.55, "bull": 0.30, "bear": 0.15},
                },
            },
        },
    }
    wire, conf = _regime_context_bundle(sig, 0.0)

    assert wire["regimeContextAsset"] == "BTC"
    assert wire["regimeContextTimeframe"] == "4h"
    assert wire["regimeContextAsOf"] == "2026-05-15T12:00:00+00:00"
    assert wire["regimeContextDurationBars"] == 7
    assert "BULL" in wire["regimeContextRunnerUp"]
    assert "30" in wire["regimeContextRunnerUp"]
    assert "regimeContextTransitionHint" in wire
    assert conf == pytest.approx(55.0)


def test_regime_context_bundle_respects_existing_regime_confidence() -> None:
    """Explicit regime_confidence is not overwritten by HMM mass."""
    sig = {
        "asset": "ETHUSDT",
        "timeframe": "1 h",
        "agent_breakdown": {
            "regime": {
                "agent_name": "regime",
                "sub_signals": {
                    "regime": "bull",
                    "duration": 3,
                    "probabilities": {"bull": 0.9, "bear": 0.05, "volatile": 0.05},
                },
            },
        },
    }
    wire, conf = _regime_context_bundle(sig, 72.0)

    assert wire["regimeContextAsset"] == "ETH"
    assert conf == 72.0
