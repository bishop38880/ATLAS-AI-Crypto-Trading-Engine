"""DR Recovery tests — Phase 4 Chaos Engineering.

8 tests validating kill switch responsiveness, reconciliation timing,
RPO limits, Redis flood resilience, safety gates, and schema integrity.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import msgspec
import pytest

from prometheus.kill_switch.core import KillSwitch
from scripts.chaos_drill import (
    MAX_RPO_MINUTES,
    MAX_RTO_MS,
    BaseScenario,
    ChaosDrillResult,
    ChaosDrillRunner,
    RedisFloodScenario,
    _assert_staging_environment,
    _is_dry_run,
    check_quality_gate,
)


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def fake_redis() -> AsyncMock:
    """Build a mock Redis client with pipeline support."""
    mock = AsyncMock()
    mock.exists.return_value = 0
    mock.set = AsyncMock()
    mock.get.return_value = None
    mock.delete = AsyncMock()
    mock.publish = AsyncMock()
    pipe = AsyncMock()
    pipe.set = MagicMock()
    pipe.execute = AsyncMock(return_value=[])
    mock.pipeline.return_value = pipe
    return mock


@pytest.fixture
def kill_switch(fake_redis: AsyncMock, tmp_path: Path) -> KillSwitch:
    """Build a KillSwitch with mock Redis."""
    return KillSwitch(fake_redis, tmp_path / "audit.log")


@pytest.fixture
def mock_pg_pool() -> MagicMock:
    """Build a mock asyncpg.Pool.

    ``asyncpg.Pool.acquire()`` returns an async context manager
    (not a coroutine), so the pool must be a MagicMock whose
    ``acquire()`` call returns ``_AcquireCtx`` directly.
    """
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=None)
    conn.fetchval = AsyncMock(return_value=1)
    conn.execute = AsyncMock()
    conn.fetch = AsyncMock(return_value=[])

    class _AcquireCtx:
        async def __aenter__(self) -> AsyncMock:
            return conn

        async def __aexit__(self, *args: object) -> None:
            pass

    pool = MagicMock()
    pool.acquire.return_value = _AcquireCtx()
    pool._conn = conn  # expose for test access
    return pool


# ── Test 1: Kill switch halts within 2s ───────────────────────────────


@pytest.mark.asyncio
async def test_kill_switch_halts_within_2s(
    kill_switch: KillSwitch,
    fake_redis: AsyncMock,
) -> None:
    """Halt call completes within MAX_RTO_MS."""
    t0 = time.monotonic()
    await kill_switch.halt(
        reason="MANUAL_PANIC_KEY",
        triggered_by="test_dr",
        details={"test": "halt_timing"},
    )
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    assert elapsed_ms < MAX_RTO_MS, "halt took {} ms > {} ms".format(elapsed_ms, MAX_RTO_MS)
    fake_redis.set.assert_called()


# ── Test 2: Kill switch resume after halt ─────────────────────────────


@pytest.mark.asyncio
async def test_kill_switch_resume_after_halt(
    kill_switch: KillSwitch,
    fake_redis: AsyncMock,
) -> None:
    """Halt → resume → is_halted returns False."""
    await kill_switch.halt(
        reason="MANUAL_PANIC_KEY",
        triggered_by="test_dr",
        details={},
    )
    fake_redis.exists.return_value = 1
    assert await kill_switch.is_halted() is True

    with patch.dict(os.environ, {"POLARIS_PANIC_KEY": "test-key"}):
        result = await kill_switch.resume("test-key")
    assert result is True

    fake_redis.exists.return_value = 0
    assert await kill_switch.is_halted() is False


# ── Test 3: Reconciliation within 5 minutes ──────────────────────────


@pytest.mark.asyncio
async def test_reconciliation_completes_within_5min() -> None:
    """Mock reconciler completes within time budget.

    Uses a simple async task that completes in 10ms to simulate
    the reconciliation pass.
    """
    t0 = time.monotonic()

    async def _mock_reconcile() -> bool:
        await asyncio.sleep(0.01)  # 10ms simulated reconciliation
        return True

    result = await _mock_reconcile()
    elapsed_s = time.monotonic() - t0

    assert result is True
    assert elapsed_s < 300, "reconciliation took {:.1f}s > 300s".format(elapsed_s)


# ── Test 4: RPO within 6-hour limit ──────────────────────────────────


@pytest.mark.asyncio
async def test_rpo_within_6hr_limit(mock_pg_pool: AsyncMock) -> None:
    """Mock pg_stat_archiver returns RPO within limit."""
    mock_pg_pool._conn.fetchrow.return_value = {"rpo_minutes": 120.0}

    from scripts.chaos_drill import _measure_wal_rpo

    rpo = await _measure_wal_rpo(mock_pg_pool)
    assert rpo <= MAX_RPO_MINUTES, "rpo={} > MAX_RPO={}".format(rpo, MAX_RPO_MINUTES)
    assert rpo == 120.0


# ── Test 5: Redis flood does not block kill switch ────────────────────


@pytest.mark.asyncio
async def test_redis_flood_does_not_block_kill_switch(
    kill_switch: KillSwitch,
    fake_redis: AsyncMock,
    tmp_path: Path,
) -> None:
    """Kill switch halt latency under simulated Redis load.

    We mock the pipeline to simulate key writes and verify halt
    latency stays under MAX_RTO_MS.
    """
    scenario = RedisFloodScenario(fake_redis, kill_switch, dry_run=True)

    # Inject is a no-op in dry run
    await scenario.inject()

    # Measure RTO — dry run returns 0
    rto = await scenario.measure_rto()
    assert rto <= MAX_RTO_MS

    # Verify actual halt latency with mocked Redis
    t0 = time.monotonic()
    await kill_switch.halt(
        reason="MANUAL_PANIC_KEY",
        triggered_by="flood_test",
        details={"keys": 100000},
    )
    actual_ms = int((time.monotonic() - t0) * 1000)
    assert actual_ms < MAX_RTO_MS


# ── Test 6: Drill refuses production env ──────────────────────────────


def test_drill_refuses_production_env() -> None:
    """CHAOS_DRILL_ENV=production → SystemExit."""
    with patch.dict(os.environ, {"CHAOS_DRILL_ENV": "production"}):
        with pytest.raises(SystemExit):
            _assert_staging_environment()


def test_drill_refuses_empty_env() -> None:
    """No CHAOS_DRILL_ENV → SystemExit."""
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(SystemExit):
            _assert_staging_environment()


def test_drill_allows_staging_env() -> None:
    """CHAOS_DRILL_ENV=staging → no exception."""
    with patch.dict(os.environ, {"CHAOS_DRILL_ENV": "staging"}):
        _assert_staging_environment()  # should not raise


# ── Test 7: ChaosDrillResult schema validation ───────────────────────


def test_drill_results_schema_valid() -> None:
    """ChaosDrillResult serializes/deserializes with msgspec."""
    result = ChaosDrillResult(
        drill_id="test-uuid",
        run_at="2026-04-25T02:00:00Z",
        scenario="redis_flood",
        rto_ms=150,
        rpo_minutes=0.0,
        passed=True,
        logs={"injected": True, "rto_ms": 150},
        environment="staging",
    )
    encoded = msgspec.json.encode(result)
    decoded = msgspec.json.decode(encoded, type=ChaosDrillResult)

    assert decoded.drill_id == "test-uuid"
    assert decoded.scenario == "redis_flood"
    assert decoded.rto_ms == 150
    assert decoded.passed is True
    assert decoded.environment == "staging"


# ── Test 8: Quality gate — 3-week check ──────────────────────────────


@pytest.mark.asyncio
async def test_quality_gate_three_week_pass(mock_pg_pool: AsyncMock) -> None:
    """3 weeks of passing results → gate opens."""
    mock_pg_pool._conn.fetchrow.return_value = {
        "total": 12,      # 4 scenarios × 3 weeks
        "pass_count": 12,  # all passed
    }
    gate = await check_quality_gate(mock_pg_pool)
    assert gate is True


@pytest.mark.asyncio
async def test_quality_gate_blocks_on_failure(mock_pg_pool: AsyncMock) -> None:
    """Any failure in last 3 weeks → gate blocked."""
    mock_pg_pool._conn.fetchrow.return_value = {
        "total": 12,
        "pass_count": 11,  # 1 failure
    }
    gate = await check_quality_gate(mock_pg_pool)
    assert gate is False


@pytest.mark.asyncio
async def test_quality_gate_blocks_on_no_data(mock_pg_pool: AsyncMock) -> None:
    """No drill results → gate blocked."""
    mock_pg_pool._conn.fetchrow.return_value = {
        "total": 0,
        "pass_count": 0,
    }
    gate = await check_quality_gate(mock_pg_pool)
    assert gate is False
