"""Nightly Dream Scheduler for ATLAS memory consolidation."""

import asyncio
from typing import Set

from loguru import logger

from atlas.rag.pattern_extractor import OutcomeAggregator, PatternSynthesiser


class CycleScheduler:
    """Schedules the nightly Dream Cycle background job.
    
    Tracks background tasks with done-callbacks to prevent memory leaks
    and ensure exceptions are logged, matching the SWR cache pattern.
    """

    def __init__(self, aggregator: OutcomeAggregator, synthesiser: PatternSynthesiser) -> None:
        """Initialize the scheduler with its dependencies."""
        self._aggregator = aggregator
        self._synthesiser = synthesiser
        self._background_tasks: Set[asyncio.Task[None]] = set()
        self._running = False

    def start(self) -> None:
        """Start the nightly scheduler loop."""
        if self._running:
            return
        self._running = True

        task = asyncio.create_task(self._dream_loop())
        self._background_tasks.add(task)
        task.add_done_callback(self._on_task_done)

    async def stop(self) -> None:
        """Stop the scheduler and cancel pending tasks."""
        self._running = False
        for task in list(self._background_tasks):
            task.cancel()

    async def _dream_loop(self) -> None:
        """Run the dream cycle every 24 hours without blocking."""
        while self._running:
            try:
                # Calculate time to next 03:00 UTC and sleep
                # (03:00 follows Agent Zero at 02:00 — clean memory first)
                sleep_seconds = self._seconds_to_target_hour(3)
                logger.info("Dream Cycle scheduled in {:.1f} hours", sleep_seconds / 3600)
                await asyncio.sleep(sleep_seconds)
                
                if not self._running:
                    break
                    
                await self._run_cycle_safe()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("Dream cycle fatal error in loop: {}", str(e))
                # Sleep a bit to prevent tight loop on repeated failure
                await asyncio.sleep(3600)

    def _seconds_to_target_hour(self, hour: int) -> float:
        """Calculate seconds until the next target UTC hour.
        
        Args:
            hour: Target UTC hour (0-23).
            
        Returns:
            Seconds to sleep until the next occurrence.
        """
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        return (target - now).total_seconds()

    async def _run_cycle_safe(self) -> None:
        """Run the actual synthesis cycle, catching all errors."""
        try:
            await self._synthesiser.run_dream_cycle(self._aggregator)
        except Exception as e:
            logger.error("Dream cycle failed (will retry tomorrow): {}", str(e))

    def _on_task_done(self, task: asyncio.Task[None]) -> None:
        """Cleanup tracking for a completed background task."""
        self._background_tasks.discard(task)
        if not task.cancelled() and task.exception():
            logger.error("Scheduler task crashed: {}", str(task.exception()))
