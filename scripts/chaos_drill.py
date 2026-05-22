"""Phase 4 — Infrastructure-level Chaos Engineering Drills.

Safety invariants:
    - Refuses to run unless ``CHAOS_DRILL_ENV=staging``.
    - Dry-run mode logs commands without executing.
    - Cleanup runs in ``finally`` blocks.

Usage::

    CHAOS_DRILL_ENV=staging python -m scripts.chaos_drill
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import time
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

import asyncpg
import msgspec
import redis.asyncio as redis_async
from loguru import logger

from prometheus.kill_switch.core import KillSwitch

MAX_RTO_MS: int = 2000
MAX_RECONCILIATION_MINUTES: int = 5
MAX_RPO_MINUTES: int = 360
_CHAOS_DRILL_PREFIX: str = "chaos_drill:"


class ChaosDrillResult(msgspec.Struct, frozen=True):
    """Persisted to ``chaos_drill_results`` PostgreSQL table."""
    drill_id: str
    run_at: str
    scenario: str
    rto_ms: int
    rpo_minutes: float
    passed: bool
    logs: dict[str, str | int | float | bool]
    environment: str


def _assert_staging_environment() -> None:
    env = os.environ.get("CHAOS_DRILL_ENV", "")
    if env != "staging":
        logger.critical("CHAOS_DRILL_ENV={} — refusing to run", env)
        raise SystemExit(1)


def _is_dry_run() -> bool:
    return os.environ.get("CHAOS_DRILL_DRY_RUN", "false").lower() == "true"


def _run_shell_sync(
    cmd: str, *, timeout_s: int = 30,
) -> subprocess.CompletedProcess[str]:
    """Blocking shell execution — must be called via asyncio.to_thread."""
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout_s)


async def _run_shell(
    cmd: str, *, timeout_s: int = 30, dry_run: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Non-blocking shell execution via ``asyncio.to_thread``."""
    if dry_run:
        logger.info("[DRY-RUN] would execute: {}", cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="[dry-run]", stderr="")
    logger.info("executing: {}", cmd)
    return await asyncio.to_thread(_run_shell_sync, cmd, timeout_s=timeout_s)


class BaseScenario(ABC):
    """Interface for infrastructure chaos scenarios."""
    name: str = "base"

    def __init__(self, dry_run: bool = False) -> None:
        self._dry_run = dry_run

    @abstractmethod
    async def inject(self) -> None: ...
    @abstractmethod
    async def measure_rto(self) -> int: ...
    @abstractmethod
    async def measure_rpo(self) -> float: ...
    @abstractmethod
    async def cleanup(self) -> None: ...

    def verify_pass(self, rto_ms: int, rpo_minutes: float) -> bool:
        rto_ok = rto_ms <= MAX_RTO_MS
        rpo_ok = rpo_minutes <= MAX_RPO_MINUTES
        if not rto_ok:
            logger.warning("FAIL: rto_ms={} > MAX_RTO_MS={}", rto_ms, MAX_RTO_MS)
        if not rpo_ok:
            logger.warning("FAIL: rpo_min={} > MAX_RPO={}", rpo_minutes, MAX_RPO_MINUTES)
        return rto_ok and rpo_ok


class KillPrimaryHostScenario(BaseScenario):
    """Stop the ATLAS Docker container and measure recovery."""
    name = "kill_primary_host"

    def __init__(
        self, redis_client: redis_async.Redis, kill_switch: KillSwitch,  # type: ignore[type-arg]
        pg_pool: asyncpg.Pool, *, dry_run: bool = False,
    ) -> None:
        super().__init__(dry_run=dry_run)
        self._redis = redis_client
        self._ks = kill_switch
        self._pg = pg_pool

    async def inject(self) -> None:
        await _run_shell('docker stop $(docker ps -q --filter "name=atlas")', dry_run=self._dry_run)

    async def measure_rto(self) -> int:
        t0 = time.monotonic()
        deadline = t0 + (MAX_RTO_MS / 1000.0) * 2
        while time.monotonic() < deadline:
            if await self._ks.is_halted():
                return int((time.monotonic() - t0) * 1000)
            await asyncio.sleep(0.05)
        return int((time.monotonic() - t0) * 1000)

    async def measure_rpo(self) -> float:
        return await _measure_wal_rpo(self._pg)

    async def cleanup(self) -> None:
        await _run_shell('docker start $(docker ps -aq --filter "name=atlas")', dry_run=self._dry_run)


class NetworkPartitionScenario(BaseScenario):
    """Partition ATLAS from PROMETHEUS via iptables."""
    name = "network_partition"

    def __init__(
        self, redis_client: redis_async.Redis, kill_switch: KillSwitch,  # type: ignore[type-arg]
        pg_pool: asyncpg.Pool, *, dry_run: bool = False,
    ) -> None:
        super().__init__(dry_run=dry_run)
        self._redis = redis_client
        self._ks = kill_switch
        self._pg = pg_pool
        self._host = os.environ.get("PROMETHEUS_HOST", "127.0.0.1")

    async def inject(self) -> None:
        await _run_shell("iptables -A INPUT -s {} -j DROP".format(self._host), dry_run=self._dry_run)

    async def measure_rto(self) -> int:
        t0 = time.monotonic()
        deadline = t0 + (MAX_RTO_MS / 1000.0) * 2
        while time.monotonic() < deadline:
            if await self._ks.is_halted():
                return int((time.monotonic() - t0) * 1000)
            await asyncio.sleep(0.05)
        return int((time.monotonic() - t0) * 1000)

    async def measure_rpo(self) -> float:
        return await _measure_wal_rpo(self._pg)

    async def cleanup(self) -> None:
        await _run_shell("iptables -D INPUT -s {} -j DROP".format(self._host), dry_run=self._dry_run)


class CorruptWALScenario(BaseScenario):
    """Truncate a WAL segment and measure recovery."""
    name = "corrupt_wal"

    def __init__(self, pg_pool: asyncpg.Pool, *, dry_run: bool = False) -> None:
        super().__init__(dry_run=dry_run)
        self._pg = pg_pool
        self._wal_file: str = ""

    async def inject(self) -> None:
        try:
            async with self._pg.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT pg_walfile_name(pg_current_wal_lsn()) AS wal_file",
                )
                if row:
                    self._wal_file = str(row["wal_file"])
        except Exception as exc:
            logger.error("wal_file_lookup_failed | exc={}", exc)
            return
        if self._wal_file:
            path = "/var/lib/postgresql/data/pg_wal/{}".format(self._wal_file)
            await _run_shell("truncate -s 50%% {}".format(path), dry_run=self._dry_run)

    async def measure_rto(self) -> int:
        t0 = time.monotonic()
        deadline = t0 + MAX_RECONCILIATION_MINUTES * 60
        while time.monotonic() < deadline:
            try:
                async with self._pg.acquire() as conn:
                    await conn.fetchval("SELECT 1")
                return int((time.monotonic() - t0) * 1000)
            except Exception as exc:
                logger.warning("corrupt_wal: pg not ready | exc={}", exc)
                await asyncio.sleep(1.0)
        return int((time.monotonic() - t0) * 1000)

    async def measure_rpo(self) -> float:
        return await _measure_wal_rpo(self._pg)

    async def cleanup(self) -> None:
        logger.info("corrupt_wal: PG crash recovery handles WAL repair")


class RedisFloodScenario(BaseScenario):
    """Flood Redis with 100k keys and verify kill switch latency."""
    name = "redis_flood"
    FLOOD_KEY_COUNT: int = 100_000

    def __init__(
        self, redis_client: redis_async.Redis, kill_switch: KillSwitch,  # type: ignore[type-arg]
        *, dry_run: bool = False,
    ) -> None:
        super().__init__(dry_run=dry_run)
        self._redis = redis_client
        self._ks = kill_switch

    async def inject(self) -> None:
        if self._dry_run:
            logger.info("[DRY-RUN] would flood {} keys", self.FLOOD_KEY_COUNT)
            return
        batch = 1000
        for start in range(0, self.FLOOD_KEY_COUNT, batch):
            pipe = self._redis.pipeline()
            for i in range(start, min(start + batch, self.FLOOD_KEY_COUNT)):
                pipe.set("{}{}".format(_CHAOS_DRILL_PREFIX, i), "payload")
            await pipe.execute()

    async def measure_rto(self) -> int:
        if self._dry_run:
            return 0
        t0 = time.monotonic()
        await self._ks.halt(
            reason="MANUAL_PANIC_KEY", triggered_by="chaos_drill",
            details={"scenario": "redis_flood", "keys": self.FLOOD_KEY_COUNT},
        )
        return int((time.monotonic() - t0) * 1000)

    async def measure_rpo(self) -> float:
        return 0.0  # Redis is ephemeral

    async def cleanup(self) -> None:
        if self._dry_run:
            return
        batch = 1000
        for start in range(0, self.FLOOD_KEY_COUNT, batch):
            keys = ["{}{}".format(_CHAOS_DRILL_PREFIX, i)
                    for i in range(start, min(start + batch, self.FLOOD_KEY_COUNT))]
            if keys:
                await self._redis.delete(*keys)
        panic_key = os.environ.get("POLARIS_PANIC_KEY", "")
        if panic_key:
            await self._ks.resume(panic_key)


# ── Shared helpers ────────────────────────────────────────────────────


async def _measure_wal_rpo(pg_pool: asyncpg.Pool) -> float:
    try:
        async with pg_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT EXTRACT(EPOCH FROM (NOW() - last_archived_time)) / 60.0 "
                "AS rpo_minutes FROM pg_stat_archiver",
            )
            if row and row["rpo_minutes"] is not None:
                return float(row["rpo_minutes"])
            return 0.0
    except Exception as exc:
        logger.error("rpo_measurement_failed | exc={}", exc)
        return MAX_RPO_MINUTES + 1


# ── Drill Orchestrator ────────────────────────────────────────────────


class ChaosDrillRunner:
    """Orchestrate all chaos scenarios sequentially."""

    def __init__(self, scenarios: list[BaseScenario], pg_pool: asyncpg.Pool) -> None:
        self._scenarios = scenarios
        self._pg = pg_pool

    async def run_all(self) -> list[ChaosDrillResult]:
        results: list[ChaosDrillResult] = []
        for scenario in self._scenarios:
            result = await self._run_single(scenario)
            results.append(result)
            await self._persist_result(result)
        return results

    async def _run_single(self, scenario: BaseScenario) -> ChaosDrillResult:
        drill_id = str(uuid.uuid4())
        run_at = datetime.now(timezone.utc).isoformat()
        logs: dict[str, str | int | float | bool] = {}
        rto_ms = -1
        rpo_minutes = -1.0
        passed = False

        try:
            logger.info("drill_start | scenario={}", scenario.name)
            await scenario.inject()
            logs["injected"] = True
            rto_ms = await scenario.measure_rto()
            rpo_minutes = await scenario.measure_rpo()
            logs["rto_ms"] = rto_ms
            logs["rpo_minutes"] = rpo_minutes
            passed = scenario.verify_pass(rto_ms, rpo_minutes)
            logs["passed"] = passed
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("drill_exception | scenario={} | exc={}", scenario.name, exc)
            logs["exception"] = str(exc)
        finally:
            try:
                await scenario.cleanup()
                logs["cleanup"] = True
            except Exception as cleanup_exc:
                logger.error("cleanup_failed | scenario={} | exc={}", scenario.name, cleanup_exc)
                logs["cleanup_error"] = str(cleanup_exc)

        return ChaosDrillResult(
            drill_id=drill_id, run_at=run_at, scenario=scenario.name,
            rto_ms=rto_ms, rpo_minutes=rpo_minutes, passed=passed,
            logs=logs, environment=os.environ.get("CHAOS_DRILL_ENV", "unknown"),
        )

    async def _persist_result(self, result: ChaosDrillResult) -> None:
        query = (
            "INSERT INTO chaos_drill_results "
            "(drill_id, run_at, scenario, rto_ms, rpo_minutes, passed, logs, environment) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)"
        )
        try:
            async with self._pg.acquire() as conn:
                await conn.execute(
                    query, result.drill_id, result.run_at, result.scenario,
                    result.rto_ms, result.rpo_minutes, result.passed,
                    msgspec.json.encode(result.logs).decode(), result.environment,
                )
        except Exception as exc:
            logger.error("persist_result_failed | exc={}", exc)


async def check_quality_gate(pg_pool: asyncpg.Pool) -> bool:
    """Verify 3 consecutive weeks of passing drills."""
    query = (
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN passed THEN 1 ELSE 0 END) AS pass_count "
        "FROM chaos_drill_results WHERE run_at >= NOW() - INTERVAL '21 days'"
    )
    try:
        async with pg_pool.acquire() as conn:
            row = await conn.fetchrow(query)
            if row is None:
                return False
            total = int(row["total"])
            pass_count = int(row["pass_count"])
            if total == 0:
                return False
            return total == pass_count
    except Exception as exc:
        logger.error("quality_gate_check_failed | exc={}", exc)
        return False


async def main() -> None:
    """Entry point for the chaos drill script."""
    _assert_staging_environment()
    dry_run = _is_dry_run()

    pg_dsn = os.environ.get("POSTGRES_DSN", "postgresql://atlas:atlas@localhost:5432/atlas_staging")
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

    pg_pool = await asyncpg.create_pool(dsn=pg_dsn)
    assert pg_pool is not None
    redis_client: redis_async.Redis = redis_async.from_url(redis_url)  # type: ignore[assignment]

    from pathlib import Path
    ks = KillSwitch(redis_client, Path("/tmp/chaos_drill_audit.log"))

    scenarios: list[BaseScenario] = [
        KillPrimaryHostScenario(redis_client, ks, pg_pool, dry_run=dry_run),
        NetworkPartitionScenario(redis_client, ks, pg_pool, dry_run=dry_run),
        CorruptWALScenario(pg_pool, dry_run=dry_run),
        RedisFloodScenario(redis_client, ks, dry_run=dry_run),
    ]

    runner = ChaosDrillRunner(scenarios, pg_pool)
    results = await runner.run_all()

    all_passed = all(r.passed for r in results)
    if not all_passed:
        from scripts.telegram_alert import send_telegram_alert
        failed = [r.scenario for r in results if not r.passed]
        await send_telegram_alert(
            "🚨 CHAOS DRILL FAILED\nFailed: {}\nPromotion BLOCKED.".format(", ".join(failed)),
        )

    gate_open = await check_quality_gate(pg_pool)
    logger.info("quality_gate={}", "OPEN" if gate_open else "BLOCKED")

    await pg_pool.close()
    await redis_client.aclose()
    if not all_passed:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
