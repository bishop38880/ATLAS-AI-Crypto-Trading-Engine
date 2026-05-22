from __future__ import annotations

import asyncio
import msgspec
import redis.asyncio as redis_async
from datetime import datetime, timezone

from loguru import logger
from pydantic import BaseModel, ConfigDict

from atlas.models.signal import SignalOutput
from atlas.shared.config import PolarisSettings

TIER1_ASSETS: frozenset[str] = frozenset({
    "BTC", "ETH", "SOL", "BNB", "XRP", "AVAX",
})

TIER2_ASSETS: frozenset[str] = frozenset({
    "DOGE", "LINK", "SUI", "INJ", "TAO", "RENDER",
    "ICP", "HBAR", "WIF", "PEPE", "ARB", "OP",
    "POL",
    "NEAR", "APT", "SEI", "TIA", "PYTH",
    "JUP", "PENDLE", "WLD", "STRK", "MANTA", "ALT",
    "PIXEL", "PORTAL", "MYRO",
})

class AllocationResult(BaseModel, frozen=True):
    allocated: list[str]
    queued: list[str]
    passed: list[str]
    locked: list[str]
    cycle_timestamp: datetime
    available_slots: int

class RiskGovernor:
    def __init__(
        self,
        settings: PolarisSettings,
        redis_client: redis_async.Redis,
        max_open_positions: int = 6,
    ) -> None:
        self._settings = settings
        self._redis = redis_client
        self._max_open_positions = getattr(settings, "max_open_positions", max_open_positions)

    async def get_open_positions(self) -> list[str]:
        try:
            raw = await asyncio.wait_for(
                self._redis.get("positions:open"), timeout=5.0
            )
            if not raw:
                return []
            return msgspec.json.decode(raw, type=list[str])
        except Exception as e:
            logger.warning("redis degraded | op={} | err={}", "get_open_positions", str(e))
            return []

    def _split_and_sort_signals(
        self, signals: dict[str, SignalOutput], locked: list[str]
    ) -> tuple[list[tuple[str, SignalOutput]], list[str]]:
        passed = []
        qualifying = []
        for asset, signal in signals.items():
            if asset in locked:
                continue
            if signal.score >= 55:
                qualifying.append((asset, signal))
            else:
                passed.append(asset)
                
        qualifying.sort(key=lambda x: (
            -x[1].score,
            -2 if x[0] in TIER1_ASSETS else (-1 if x[0] in TIER2_ASSETS else 0),
            x[0]
        ))
        return qualifying, passed

    async def rank_and_allocate(
        self,
        signals: dict[str, SignalOutput],
    ) -> AllocationResult:
        cycle_timestamp = datetime.now(timezone.utc)
        if not signals:
            return AllocationResult(
                allocated=[], queued=[], passed=[], locked=[],
                cycle_timestamp=cycle_timestamp, available_slots=self._max_open_positions,
            )

        open_positions = await self.get_open_positions()
        locked = [asset for asset in open_positions if asset in signals]
        available_slots = max(0, self._max_open_positions - len(locked))

        qualifying, passed = self._split_and_sort_signals(signals, locked)

        allocated = []
        queued = []
        for asset, _ in qualifying:
            if len(allocated) < available_slots:
                allocated.append(asset)
            else:
                queued.append(asset)

        return AllocationResult(
            allocated=allocated, queued=queued, passed=passed, locked=open_positions,
            cycle_timestamp=cycle_timestamp, available_slots=available_slots,
        )
