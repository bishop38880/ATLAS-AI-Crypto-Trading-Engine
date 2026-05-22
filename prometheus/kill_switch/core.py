"""Emergency kill switch — standalone, documented scoped override.

This module has NO dependency on ATLAS.  It imports from
``prometheus/kill_switch/`` only.

Architecture invariant (Invariant 3 — scoped override):
    ``_expected_key()`` reads ``os.environ`` for the panic key ONLY,
    because a failure of ``PolarisSettings`` itself (e.g. corrupted
    ``.env``) must not disable the emergency halt.  All other settings
    access uses ``PolarisSettings``.
"""

import asyncio
import hmac
import os
from datetime import datetime, timezone
from pathlib import Path

import msgspec
import redis.asyncio as redis_async
from loguru import logger

from prometheus.kill_switch.schemas import SystemHaltEvent

_PANIC_KEY_ENV = "POLARIS_PANIC_KEY"
_HALT_REDIS_KEY = "prometheus:trading_halted"
_RESUME_REDIS_KEY = "prometheus:resume_trigger"
_HALT_CHANNEL = "prometheus:system_halt"


class KillSwitch:
    """Last-line-of-defense circuit breaker for PROMETHEUS.

    Works independently of ATLAS, the main event loop, and all agents.
    Halt state persists in Redis until explicitly cleared via an
    authenticated resume command.
    """

    def __init__(
        self,
        redis_client: redis_async.Redis,  # type: ignore[type-arg]
        audit_log_path: Path,
    ) -> None:
        self._redis = redis_client
        self._audit_path = audit_log_path

    def _expected_key(self) -> str | None:
        """Scoped ``os.environ`` read — documented override per Invariant 3.

        This is the ONLY place in the kill switch that reads from
        ``os.environ`` directly.  If the panic key is not set, halt
        commands via panic key are blocked.
        """
        return os.environ.get(_PANIC_KEY_ENV)

    def verify_panic_key(self, provided: str) -> bool:
        """Constant-time HMAC comparison — timing-attack safe.

        Uses ``hmac.compare_digest()`` — NEVER ``==``.
        """
        expected = self._expected_key()
        if expected is None:
            logger.error("panic key not configured — halt BLOCKED")
            return False
        return hmac.compare_digest(provided.encode(), expected.encode())

    async def halt(
        self,
        reason: str,
        triggered_by: str,
        details: dict[str, str | int | float],
    ) -> None:
        """Canonical halt — sets Redis key, publishes channel event, writes audit."""
        event = SystemHaltEvent(
            event_type="HALT",
            reason=reason,  # type: ignore[arg-type]
            triggered_by=triggered_by,
            timestamp_iso=datetime.now(timezone.utc).isoformat(),
            details={k: str(v) for k, v in details.items()},
        )
        encoded = msgspec.json.encode(event)
        await self._redis.set(_HALT_REDIS_KEY, encoded)
        await self._redis.publish(_HALT_CHANNEL, encoded)
        await self._append_audit(encoded + b"\n")
        logger.critical(
            "SYSTEM HALTED | reason={} | triggered_by={}",
            reason,
            triggered_by,
        )

    async def resume(self, authenticated_key: str) -> bool:
        """One-shot resume — requires fresh panic key each time.

        Resume is a separate operation from halt.  The
        ``prometheus:resume_trigger`` key is one-shot with a 60-second
        TTL and does NOT clear automatically — the halt key must be
        explicitly deleted.
        """
        if not self.verify_panic_key(authenticated_key):
            logger.error("resume denied — invalid panic key")
            return False
        event = SystemHaltEvent(
            event_type="RESUME",
            reason="MANUAL_RESUME",
            triggered_by="human_operator",
            timestamp_iso=datetime.now(timezone.utc).isoformat(),
            details={},
        )
        encoded = msgspec.json.encode(event)
        await self._redis.delete(_HALT_REDIS_KEY)
        await self._redis.set(_RESUME_REDIS_KEY, encoded, ex=60)
        await self._redis.publish(_HALT_CHANNEL, encoded)
        await self._append_audit(encoded + b"\n")
        logger.warning("SYSTEM RESUMED")
        return True

    async def is_halted(self) -> bool:
        """Check whether the system is currently in a halted state."""
        result: int = await self._redis.exists(_HALT_REDIS_KEY)
        return result > 0

    async def _append_audit(self, payload: bytes) -> None:
        """Disk write via ``asyncio.to_thread`` — no ``aiofiles``."""

        def _append() -> None:
            with self._audit_path.open("ab") as f:
                f.write(payload)

        await asyncio.to_thread(_append)
