"""HTTP fetch retry with exponential backoff and jitter."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

import httpx
from loguru import logger

T = TypeVar("T")

_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


async def fetch_with_backoff(
    operation: Callable[[], Awaitable[T]],
    *,
    max_attempts: int,
    base_delay_s: float,
    max_delay_s: float,
    label: str,
    retry_on_rate_limit: bool = True,
) -> T:
    """Run ``operation`` with exponential backoff + jitter on transient failures."""
    retryable = _RETRYABLE_STATUS if retry_on_rate_limit else _RETRYABLE_STATUS - {429}
    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        try:
            return await operation()
        except asyncio.CancelledError:
            raise
        except httpx.HTTPStatusError as exc:
            last_exc = exc
            if exc.response.status_code not in retryable:
                raise
        except (httpx.TimeoutException, asyncio.TimeoutError) as exc:
            last_exc = exc
        except Exception as exc:
            last_exc = exc
            raise

        if attempt >= max_attempts - 1:
            break
        delay = min(max_delay_s, base_delay_s * (2**attempt))
        jitter = random.uniform(0.0, delay * 0.25)
        sleep_s = delay + jitter
        logger.warning(
            "monitoring_fetch_retry | label={} | attempt={} | sleep_s={}",
            label,
            attempt + 1,
            round(sleep_s, 3),
        )
        await asyncio.sleep(sleep_s)

    assert last_exc is not None
    raise last_exc
