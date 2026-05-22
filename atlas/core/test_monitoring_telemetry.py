"""Tests for monitoring Redis telemetry writers."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import msgspec
import pytest

from atlas.core.monitoring_telemetry import (
    build_latency_current_payload,
    build_startup_sequence_payload,
    publish_agent_zero_schedule,
    record_llm_cost,
    record_monitoring_alert,
    record_pipeline_cycle,
)


@pytest.fixture
async def fake_redis():
    import fakeredis.aioredis

    client = fakeredis.aioredis.FakeRedis(decode_responses=False)
    yield client
    await client.flushall()


def test_build_startup_sequence_payload_maps_statuses() -> None:
    report = SimpleNamespace(
        completed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        steps=[
            SimpleNamespace(
                step=1,
                name="Redis",
                success=True,
                duration_ms=100.0,
                detail="OK",
                critical=True,
            ),
            SimpleNamespace(
                step=2,
                name="Agents",
                success=False,
                duration_ms=50.0,
                detail="warming",
                critical=False,
            ),
        ],
    )

    payload = build_startup_sequence_payload(report)

    assert payload["overallStatus"] == "HAS_DEGRADED"
    assert payload["totalSeconds"] == 0.15
    assert payload["steps"][0]["status"] == "PASSED"
    assert payload["steps"][1]["status"] == "DEGRADED"


@pytest.mark.asyncio
async def test_record_pipeline_cycle_writes_latency_and_counters(fake_redis) -> None:
    await record_pipeline_cycle(
        fake_redis,
        cycle_id="cycle-1",
        asset="BTC/USDT",
        latency_ms=120.0,
        status="complete",
        score=72.5,
        target_ms=100,
    )

    cycle_count = await fake_redis.get("polaris:cycle_count")
    signals_emitted = await fake_redis.get("polaris:signals_emitted")
    current_raw = await fake_redis.get("polaris:latency:current")
    history = await fake_redis.lrange("polaris:latency:history", 0, 0)

    assert cycle_count == b"1"
    assert signals_emitted == b"1"
    assert len(history) == 1
    current = msgspec.json.decode(current_raw)
    assert current["current"]["p50Ms"] == 120.0
    assert current["breachCount24h"] == 1


@pytest.mark.asyncio
async def test_build_latency_current_payload_aggregates_history(fake_redis) -> None:
    for latency in (10.0, 20.0, 200.0):
        await fake_redis.lpush(
            "polaris:latency:history",
            msgspec.json.encode({"latencyMs": latency}),
        )

    payload = await build_latency_current_payload(fake_redis, target_ms=100)

    assert payload["current"]["maxMs"] == 200.0
    assert payload["breachCount24h"] == 1
    assert payload["breachPercent24h"] > 30


@pytest.mark.asyncio
async def test_record_monitoring_alert_dedupes(fake_redis) -> None:
    for _ in range(2):
        await record_monitoring_alert(
            fake_redis,
            level="WARN",
            message="Agent degraded",
            source="agent-health",
            alert_id="agent:degraded",
        )

    alerts = await fake_redis.lrange("polaris:alerts", 0, 10)

    assert len(alerts) == 1
    decoded = msgspec.json.decode(alerts[0])
    assert decoded["level"] == "WARN"


@pytest.mark.asyncio
async def test_publish_agent_zero_schedule_writes_payload(fake_redis) -> None:
    await publish_agent_zero_schedule(
        fake_redis,
        target_hour_utc=2,
        next_run_iso="2026-01-01T02:00:00+00:00",
        next_run_relative="in 1h 0m",
        last_run={"status": "PASSED"},
    )

    raw = await fake_redis.get("polaris:agent_zero:schedule")
    payload = msgspec.json.decode(raw)

    assert payload["scheduleCron"] == "0 2 * * *"
    assert payload["lastRun"]["status"] == "PASSED"


@pytest.mark.asyncio
async def test_record_llm_cost_updates_daily_breakdown(fake_redis) -> None:
    await record_llm_cost(
        fake_redis,
        provider="deepseek-chat",
        cost_usd=0.125,
        cost_cap_usd=10.0,
    )

    total = await fake_redis.get("polaris:api_cost_today")
    cap = await fake_redis.get("polaris:api_cost_cap")
    breakdown_raw = await fake_redis.get("polaris:api_cost_breakdown")
    breakdown = msgspec.json.decode(breakdown_raw)

    assert float(total) == pytest.approx(0.125)
    assert cap == b"10.00"
    assert breakdown["deepseek-chat"]["calls"] == 1
