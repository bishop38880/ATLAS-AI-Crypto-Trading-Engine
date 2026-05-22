"""Tests for fetch retry backoff."""

from __future__ import annotations

import pytest
import httpx

from atlas.monitoring.retry_policy import fetch_with_backoff


@pytest.mark.asyncio
async def test_retries_on_429_then_succeeds() -> None:
    attempts = 0

    async def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 2:
            request = httpx.Request("GET", "https://example.com")
            response = httpx.Response(429, request=request)
            raise httpx.HTTPStatusError("rate limited", request=request, response=response)
        return "ok"

    result = await fetch_with_backoff(
        operation,
        max_attempts=3,
        base_delay_s=0.01,
        max_delay_s=0.05,
        label="test",
    )
    assert result == "ok"
    assert attempts == 2


@pytest.mark.asyncio
async def test_429_not_retried_when_rate_limit_disabled() -> None:
    attempts = 0

    async def operation() -> str:
        nonlocal attempts
        attempts += 1
        request = httpx.Request("GET", "https://example.com")
        response = httpx.Response(429, request=request)
        raise httpx.HTTPStatusError("rate limited", request=request, response=response)

    with pytest.raises(httpx.HTTPStatusError):
        await fetch_with_backoff(
            operation,
            max_attempts=4,
            base_delay_s=0.01,
            max_delay_s=0.05,
            label="test",
            retry_on_rate_limit=False,
        )
    assert attempts == 1
