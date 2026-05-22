"""Agent Zero Scheduler — nightly background task at 02:00 UTC.

Runs the MemoryArchiver as a non-blocking ``asyncio.Task``. If the
database is locked or Redis times out, the error is logged and the
task silently retries the next night. NEVER crashes the Multi-Asset
Runner.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Set

from loguru import logger
import redis.asyncio as redis_async

from atlas.core.monitoring_telemetry import publish_agent_zero_schedule, safe_monitoring_write
from atlas.rag.agent_zero.archiver import MemoryArchiver


class AgentZeroScheduler:
    """Schedules the nightly Agent Zero background job.

    Tracks background tasks with done-callbacks to prevent memory leaks
    and ensure exceptions are logged.

    Args:
        archiver: The MemoryArchiver instance.
        target_hour_utc: UTC hour to run (default 2 → 02:00).
    """

    def __init__(
        self,
        archiver: MemoryArchiver,
        target_hour_utc: int = 2,
        redis_client: redis_async.Redis | None = None,  # type: ignore[type-arg]
    ) -> None:
        """Initialise with archiver and target hour."""
        self._archiver = archiver
        self._target_hour = target_hour_utc
        self._redis = redis_client
        self._last_run: dict[str, Any] | None = None
        self._background_tasks: Set[asyncio.Task[None]] = set()
        self._running = False

    def start(self) -> None:
        """Start the nightly scheduler loop."""
        if self._running:
            return
        self._running = True

        task = asyncio.create_task(self._agent_zero_loop())
        self._background_tasks.add(task)
        task.add_done_callback(self._on_task_done)
        logger.info(
            "Agent Zero scheduler started | target={}:00 UTC",
            self._target_hour,
        )

    async def stop(self) -> None:
        """Stop the scheduler and cancel pending tasks."""
        self._running = False
        for task in list(self._background_tasks):
            task.cancel()

    async def _agent_zero_loop(self) -> None:
        """Run Agent Zero every 24h at the configured UTC hour."""
        while self._running:
            try:
                sleep_seconds = self._seconds_to_target()
                logger.info(
                    "Agent Zero scheduled in {:.1f} hours",
                    sleep_seconds / 3600,
                )
                await self._publish_schedule(sleep_seconds)
                await asyncio.sleep(sleep_seconds)

                if not self._running:
                    break

                await self._run_safe()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(
                    "Agent Zero loop error (will retry tomorrow): {}",
                    str(exc),
                )
                await asyncio.sleep(3600)

    def _seconds_to_target(self) -> float:
        """Calculate seconds until the next target hour UTC.

        Returns:
            Seconds to sleep before the next run.
        """
        now = datetime.now(timezone.utc)
        target_today = now.replace(
            hour=self._target_hour, minute=0, second=0, microsecond=0,
        )
        if now >= target_today:
            target_today += timedelta(days=1)
        return (target_today - now).total_seconds()

    async def _run_safe(self) -> None:
        """Execute the nightly cycle with broad exception handling."""
        started = datetime.now(timezone.utc)
        try:
            summary = await self._archiver.run_nightly_cycle()
            finished = datetime.now(timezone.utc)
            self._last_run = {
                "timestamp": finished.isoformat(),
                "status": "PASSED",
                "durationSeconds": (finished - started).total_seconds(),
                "documentsScored": summary.total_scored,
                "archived": summary.archived_count,
                "retained": summary.retained_count,
                "errors": summary.error_count,
            }
        except Exception as exc:
            finished = datetime.now(timezone.utc)
            self._last_run = {
                "timestamp": finished.isoformat(),
                "status": "FAILED",
                "durationSeconds": (finished - started).total_seconds(),
                "error": str(exc),
            }
            logger.error(
                "Agent Zero cycle failed (will retry tomorrow): {}",
                str(exc),
            )
        await self._publish_schedule(self._seconds_to_target())

    async def _publish_schedule(self, sleep_seconds: float) -> None:
        """Publish scheduler metadata when Redis is available."""
        if self._redis is None:
            return

        next_run = datetime.now(timezone.utc) + timedelta(seconds=sleep_seconds)
        await safe_monitoring_write(
            publish_agent_zero_schedule(
                self._redis,
                target_hour_utc=self._target_hour,
                next_run_iso=next_run.isoformat(),
                next_run_relative=_format_relative_seconds(sleep_seconds),
                last_run=self._last_run,
            ),
            action="publish_agent_zero_schedule",
        )

    def _on_task_done(self, task: asyncio.Task[None]) -> None:
        """Cleanup tracking for a completed background task."""
        self._background_tasks.discard(task)
        if not task.cancelled() and task.exception():
            logger.error(
                "Agent Zero task crashed: {}", str(task.exception()),
            )


def _format_relative_seconds(seconds: float) -> str:
    """Format a coarse relative duration for dashboard display."""
    safe_seconds = max(0, int(seconds))
    hours = safe_seconds // 3600
    minutes = (safe_seconds % 3600) // 60
    if hours > 0:
        return f"in {hours}h {minutes}m"
    return f"in {minutes}m"
