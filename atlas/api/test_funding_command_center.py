"""Tests for Funding Command Centre helpers and Redis-backed snapshot."""

from __future__ import annotations

from decimal import Decimal

import msgspec
import pytest

from atlas.api.funding_command_center import (
    annualized_simple_funding_pct,
    build_dashboard_33_pairs,
    calculate_funding_zscore,
    classify_funding_flip,
    derive_pair_from_rotation_entry,
    fetch_funding_command_center_snapshot,
    pair_to_compact_exchange,
)


def test_derive_pair_and_compact_exchange() -> None:
    """Rotation-style entries normalise consistently."""

    assert derive_pair_from_rotation_entry("BTCUSDT") == "BTC/USDT"
    assert derive_pair_from_rotation_entry("btc/usdt") == "BTC/USDT"
    assert derive_pair_from_rotation_entry("ETH-PERP") == "ETH/USDT"
    assert pair_to_compact_exchange("BTC/USDT") == "BTCUSDT"


def test_build_dashboard_pairs_falls_back_when_redis_missing() -> None:
    """Empty rotation yields deterministic 33-slot ladder."""

    rows = build_dashboard_33_pairs([])
    assert len(rows) == 33
    assert rows[0].upper() == "BTC/USDT".upper()


def test_annualised_simple_multiplier() -> None:
    """0.01% per 8h → simple annual headline."""

    rate = Decimal("0.0001")
    pct = annualized_simple_funding_pct(rate)
    assert pct == Decimal("0.0001") * Decimal("3") * Decimal("365") * Decimal("100")


def test_calculate_funding_zscore_basic() -> None:
    """Z-score aligns with textbook mean/variance."""

    hist = [
        Decimal("0.01"),
        Decimal("0"),
        Decimal("-0.01"),
        Decimal("0.02"),
    ]
    cur = Decimal("0.06")
    z = calculate_funding_zscore(cur, hist)
    assert z > 2.4


@pytest.mark.parametrize(
    ("zscore", "want_kind"),
    [
        (2.9, "COMPRESS_FROM_POSITIVE"),
        (-2.9, "COMPRESS_FROM_NEGATIVE"),
        (0.1, "NEUTRAL"),
    ],
)
def test_classify_flip_kinds(zscore: float, want_kind: str) -> None:
    """Flip labels saturate at tails."""

    kind, strength, _expl = classify_funding_flip(zscore)
    assert kind == want_kind
    assert 0 <= strength <= 100


@pytest.mark.asyncio
async def test_fetch_snapshot_reads_okx_pipeline_keys() -> None:
    """Snapshot hydrates OKX MCP JSON blobs from Redis."""

    import fakeredis.aioredis

    redis_client = fakeredis.aioredis.FakeRedis(decode_responses=False)

    history_payload = {
        "inst_id": "BTC-USDT-SWAP",
        "bars": [
            {
                "inst_id": "BTC-USDT-SWAP",
                "funding_rate": "0",
                "funding_time": 1_700_000_000_000,
                "realized_rate": "0",
            },
            {
                "inst_id": "BTC-USDT-SWAP",
                "funding_rate": "0",
                "funding_time": 1_700_028_800_000,
                "realized_rate": "0",
            },
            {
                "inst_id": "BTC-USDT-SWAP",
                "funding_rate": "0",
                "funding_time": 1_700_057_600_000,
                "realized_rate": "0",
            },
            {
                "inst_id": "BTC-USDT-SWAP",
                "funding_rate": "0",
                "funding_time": 1_700_086_400_000,
                "realized_rate": "0",
            },
            {
                "inst_id": "BTC-USDT-SWAP",
                "funding_rate": "0.00003",
                "funding_time": 1_700_115_200_000,
                "realized_rate": "0",
            },
        ],
        "source": "okx_mcp",
    }

    funding_rate_payload = {
        "inst_id": "BTC-USDT-SWAP",
        "funding_rate": "0.001",
        "funding_time": 1_700_144_000_000,
        "next_funding_time": 1_700_172_800_000,
        "min_funding_rate": "-0.02",
        "max_funding_rate": "0.02",
        "source": "okx_mcp",
        "fetched_at_ms": 1,
    }

    await redis_client.set(
        "provider:okx_mcp:BTCUSDT:funding_history",
        msgspec.json.encode(history_payload),
    )
    await redis_client.set(
        "provider:okx_mcp:BTCUSDT:funding_rate",
        msgspec.json.encode(funding_rate_payload),
    )

    snap = await fetch_funding_command_center_snapshot(redis_client)
    assert len(snap.dashboard_pairs_used) == 33
    btc_rows = [r for r in snap.rows if r.asset.upper() == "BTC/USDT".upper()]
    assert len(btc_rows) == 1
    btc_row = btc_rows[0]
    assert btc_row.funding_rate_8h == "0.001"
    assert btc_row.cache_hit is True
    assert len(btc_row.history) == 5
    assert snap.rows[0].compact_symbol == "BTCUSDT"
