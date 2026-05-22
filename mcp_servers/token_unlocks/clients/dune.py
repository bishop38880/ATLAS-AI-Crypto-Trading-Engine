"""Dune Analytics async client — Layer 2 on-chain vesting data.

ATLAS Intelligence Layer — NewsMacroAgent Integration.

Implements the resilient submit-and-poll execution loop required by
Dune's asynchronous API:

1. ``POST /v1/query/{query_id}/execute`` → submit execution.
2. Poll ``GET /v1/execution/{execution_id}/status`` with exponential
   backoff until ``QUERY_STATE_COMPLETED``.
3. ``GET /v1/execution/{execution_id}/results`` → extract token amounts.

Free-tier constraints are respected via ``asyncio.sleep`` with capped
exponential backoff (max 30 s between polls).

All token amounts are ``Decimal`` — float is banned.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
import msgspec
from loguru import logger

from mcp_servers.token_unlocks.models import (
    DuneUnlockEvent,
    DuneUnlockSchedule,
)

_BASE_URL = "https://api.dune.com/api/v1"
_TIMEOUT = httpx.Timeout(15.0, connect=5.0)
_LIMITS = httpx.Limits(
    max_connections=5,
    max_keepalive_connections=3,
    keepalive_expiry=30.0,
)

# Exponential backoff parameters for the poll loop.
_INITIAL_POLL_INTERVAL_S = 2.0
_MAX_POLL_INTERVAL_S = 30.0
_BACKOFF_FACTOR = 1.5
_DEFAULT_MAX_ATTEMPTS = 20
_DEFAULT_POLL_TIMEOUT_S = 120.0

_COMPLETED_STATE = "QUERY_STATE_COMPLETED"
_FAILED_STATES = frozenset({
    "QUERY_STATE_FAILED",
    "QUERY_STATE_CANCELLED",
    "QUERY_STATE_EXPIRED",
})


def _to_decimal(value: Any, default: str = "0") -> Decimal:
    """Safely cast any value to ``Decimal``."""
    if value is None:
        return Decimal(default)
    try:
        result = Decimal(str(value))
        if result.is_nan() or result.is_infinite():
            return Decimal(default)
        return result
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


class DuneClient:
    """Async HTTP client for Dune Analytics execution API.

    ATLAS Intelligence Layer — NewsMacroAgent.

    Exit Liquidity Guardrail: This client fetches exact on-chain vesting
    contract movements so the penalty engine can determine whether an
    impending cliff unlock threatens to overwhelm circulating supply.

    The ``fetch_unlocks`` method orchestrates the full submit → poll →
    fetch lifecycle for a given Dune query ID.
    """

    def __init__(
        self,
        api_key: str,
        max_poll_attempts: int = _DEFAULT_MAX_ATTEMPTS,
        poll_timeout_seconds: float = _DEFAULT_POLL_TIMEOUT_S,
    ) -> None:
        self._api_key = api_key
        self._max_poll_attempts = max_poll_attempts
        self._poll_timeout_s = poll_timeout_seconds
        self._client = httpx.AsyncClient(
            http2=True,
            timeout=_TIMEOUT,
            limits=_LIMITS,
            headers={
                "X-Dune-API-Key": api_key,
                "User-Agent": "ATLAS-TokenUnlocks/1.0",
            },
        )

    async def close(self) -> None:
        """Release pooled connections."""
        await self._client.aclose()

    async def fetch_unlocks(
        self, symbol: str, dune_query_id: int,
    ) -> DuneUnlockSchedule:
        """Execute a Dune query and return normalised unlock events.

        Orchestrates: submit → poll → fetch → parse.

        Args:
            symbol: Uppercase ticker (e.g. ``"ARB"``).
            dune_query_id: The Dune query ID to execute.

        Returns:
            ``DuneUnlockSchedule`` with events and aggregate totals.
        """
        execution_id = await self._submit_execution(dune_query_id)
        if not execution_id:
            return self._degraded_schedule(symbol, dune_query_id)

        completed = await self._poll_until_complete(execution_id)
        if not completed:
            return self._degraded_schedule(
                symbol, dune_query_id, execution_id,
            )

        rows = await self._fetch_results(execution_id)
        return self._parse_results(
            symbol, dune_query_id, execution_id, rows,
        )

    # ─── Step 1: Submit ──────────────────────────────────────────────

    async def _submit_execution(
        self, query_id: int,
    ) -> str:
        """Submit a query for execution via POST.

        Returns the ``execution_id`` or empty string on failure.
        """
        url = f"{_BASE_URL}/query/{query_id}/execute"
        try:
            response = await asyncio.wait_for(
                self._client.post(url),
                timeout=15.0,
            )
            response.raise_for_status()
            body = msgspec.json.decode(response.content)
            execution_id = body.get("execution_id", "")
            logger.info(
                "Dune query submitted | query_id={} | exec_id={}",
                query_id, execution_id,
            )
            return str(execution_id)
        except asyncio.CancelledError:
            raise
        except httpx.HTTPStatusError as exc:
            logger.error(
                "Dune submit HTTP error | query_id={} | status={}",
                query_id, exc.response.status_code,
            )
            return ""
        except Exception as exc:
            logger.exception(
                "Dune submit failed | query_id={}", query_id,
            )
            return ""

    # ─── Step 2: Poll ────────────────────────────────────────────────

    async def _poll_until_complete(
        self, execution_id: str,
    ) -> bool:
        """Poll execution status with exponential backoff.

        Returns ``True`` when the query completes successfully,
        ``False`` on failure, timeout, or cancellation.
        """
        url = f"{_BASE_URL}/execution/{execution_id}/status"
        interval = _INITIAL_POLL_INTERVAL_S

        for attempt in range(1, self._max_poll_attempts + 1):
            state = await self._check_execution_state(
                url, execution_id, attempt,
            )
            if state == _COMPLETED_STATE:
                return True
            if state in _FAILED_STATES:
                logger.error(
                    "Dune query failed | exec_id={} | state={}",
                    execution_id, state,
                )
                return False
            if state == "":
                return False  # Network failure

            await asyncio.sleep(interval)
            interval = min(
                interval * _BACKOFF_FACTOR, _MAX_POLL_INTERVAL_S,
            )

        logger.error(
            "Dune poll exhausted | exec_id={} | attempts={}",
            execution_id, self._max_poll_attempts,
        )
        return False

    async def _check_execution_state(
        self, url: str, execution_id: str, attempt: int,
    ) -> str:
        """Single poll attempt. Returns the state string."""
        try:
            response = await asyncio.wait_for(
                self._client.get(url),
                timeout=10.0,
            )
            response.raise_for_status()
            body = msgspec.json.decode(response.content)
            state = body.get("state", "")
            logger.debug(
                "Dune poll | exec_id={} | attempt={} | state={}",
                execution_id, attempt, state,
            )
            return str(state)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "Dune poll error | exec_id={} | attempt={} | err={}",
                execution_id, attempt, exc,
            )
            return ""

    # ─── Step 3: Fetch Results ───────────────────────────────────────

    async def _fetch_results(
        self, execution_id: str,
    ) -> list[dict[str, Any]]:
        """Fetch completed query results."""
        url = f"{_BASE_URL}/execution/{execution_id}/results"
        try:
            response = await asyncio.wait_for(
                self._client.get(url),
                timeout=15.0,
            )
            response.raise_for_status()
            body = msgspec.json.decode(response.content)
            result = body.get("result", {})
            rows = result.get("rows", [])
            logger.info(
                "Dune results fetched | exec_id={} | rows={}",
                execution_id, len(rows),
            )
            return rows if isinstance(rows, list) else []
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception(
                "Dune results fetch failed | exec_id={}",
                execution_id,
            )
            return []

    # ─── Parsing & Assembly ──────────────────────────────────────────

    def _parse_results(
        self,
        symbol: str,
        query_id: int,
        execution_id: str,
        rows: list[dict[str, Any]],
    ) -> DuneUnlockSchedule:
        """Parse raw Dune rows into typed ``DuneUnlockEvent`` list."""
        events: list[DuneUnlockEvent] = []
        total_amount = Decimal("0")
        total_usd = Decimal("0")

        for row in rows:
            event = self._parse_single_row(symbol, row)
            events.append(event)
            total_amount += event.unlock_amount
            total_usd += event.unlock_usd_value

        now_utc = datetime.now(timezone.utc).isoformat()
        return DuneUnlockSchedule(
            symbol=symbol,
            events=events,
            total_unlock_amount=total_amount,
            total_unlock_usd=total_usd,
            query_id=query_id,
            execution_id=execution_id,
            fetched_at=now_utc,
            status="OK",
        )

    @staticmethod
    def _parse_single_row(
        symbol: str, row: dict[str, Any],
    ) -> DuneUnlockEvent:
        """Parse one Dune result row into a ``DuneUnlockEvent``."""
        unlock_amount = _to_decimal(
            row.get("unlock_amount", row.get("amount")),
        )
        unlock_usd = _to_decimal(
            row.get("unlock_usd_value", row.get("usd_value")),
        )
        hours = _to_decimal(
            row.get("hours_until_unlock", row.get("hours_remaining")),
        )
        unlock_dt = str(
            row.get("unlock_datetime", row.get("unlock_date", "")),
        )
        unlock_type = str(
            row.get("unlock_type", row.get("type", "UNKNOWN")),
        ).upper()
        beneficiary = str(row.get("beneficiary", ""))

        return DuneUnlockEvent(
            symbol=symbol,
            unlock_amount=unlock_amount,
            unlock_usd_value=unlock_usd,
            unlock_datetime=unlock_dt,
            hours_until_unlock=hours,
            unlock_type=unlock_type,
            beneficiary=beneficiary,
        )

    @staticmethod
    def _degraded_schedule(
        symbol: str,
        query_id: int,
        execution_id: str = "",
    ) -> DuneUnlockSchedule:
        """Return a safe empty schedule on failure."""
        now_utc = datetime.now(timezone.utc).isoformat()
        return DuneUnlockSchedule(
            symbol=symbol,
            query_id=query_id,
            execution_id=execution_id,
            fetched_at=now_utc,
            status="DEGRADED",
        )
