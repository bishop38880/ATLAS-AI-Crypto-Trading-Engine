"""Tests for Risk Governor snapshot builder and veto journal."""

from __future__ import annotations

import msgspec
import pytest

from atlas.api.risk_governor_logic import build_risk_governor_snapshot
from atlas.core.risk_veto_journal import append_risk_veto_event, load_risk_veto_events_raw
from atlas.shared.config import PolarisSettings


@pytest.fixture
async def fake_redis():
    import fakeredis.aioredis

    client = fakeredis.aioredis.FakeRedis(decode_responses=False)
    yield client
    await client.flushall()


@pytest.mark.asyncio
async def test_snapshot_defaults(fake_redis) -> None:
    snap = await build_risk_governor_snapshot(fake_redis, PolarisSettings())
    assert snap.total_portfolio_exposure_pct == 0.0
    assert snap.trading_halted is False
    assert len(snap.tier_exposure) == 5
    assert snap.veto_events_30d_total == 0


@pytest.mark.asyncio
async def test_snapshot_reads_portfolio_keys(fake_redis) -> None:
    await fake_redis.set(b"portfolio:total_exposure_pct", b"0.42")
    await fake_redis.set(b"portfolio:daily_drawdown_pct", b"0.02")
    await fake_redis.set(b"portfolio:weekly_drawdown_pct", b"0.04")
    await fake_redis.set(
        b"portfolio:exposure_by_tier",
        msgspec.json.encode(
            {"core": 0.1, "majors": 0.2, "l1_l2": 0.05, "defi": 0.02, "rotation": 0.05},
        ),
    )
    snap = await build_risk_governor_snapshot(fake_redis, PolarisSettings())
    assert snap.total_portfolio_exposure_pct == pytest.approx(42.0)
    assert snap.daily_drawdown_pct == pytest.approx(2.0)
    majors = next(r for r in snap.tier_exposure if r.tier_id == "majors")
    assert majors.exposure_pct == pytest.approx(20.0)


@pytest.mark.asyncio
async def test_veto_journal_roundtrip(fake_redis) -> None:
    await append_risk_veto_event(
        fake_redis,
        asset="BTC/USDT",
        reasons=["Tier-4 cascade occurring: FAST-PATH VETO"],
        cycle_id="c1",
    )
    rows = await load_risk_veto_events_raw(fake_redis)
    assert len(rows) == 1
    assert rows[0]["asset"] == "BTC/USDT"
    assert "FAST-PATH" in rows[0]["reasons"][0]

    snap = await build_risk_governor_snapshot(fake_redis, PolarisSettings())
    assert snap.veto_events_30d_total >= 1
    assert len(snap.recent_vetoes) >= 1
