from __future__ import annotations

import asyncio

import pytest
from fakeredis import FakeAsyncRedis
from fastapi import FastAPI

from backend.main import _start_paper_trade_executor, _stop_paper_trade_executor
from prometheus.settings import PrometheusSettings


@pytest.mark.asyncio
async def test_paper_trade_executor_lifecycle_starts_and_stops() -> None:
    app = FastAPI()
    redis = FakeAsyncRedis()
    settings = PrometheusSettings(
        _env_file=None,
        paper_trade_executor_enabled=True,
        paper_trade_dry_run=True,
        bitget_demo_write_enabled=False,
    )

    await _start_paper_trade_executor(app, redis, settings)
    task = app.state.paper_trade_executor_task
    http_client = app.state.paper_trade_http_client

    await asyncio.sleep(0.05)

    assert task is not None
    assert not task.done()
    assert http_client is not None

    await _stop_paper_trade_executor(app)

    assert task.done()
    assert http_client.is_closed
    assert app.state.paper_trade_executor_task is None
    assert app.state.paper_trade_http_client is None


@pytest.mark.asyncio
async def test_paper_trade_executor_lifecycle_respects_disabled_flag() -> None:
    app = FastAPI()
    redis = FakeAsyncRedis()
    settings = PrometheusSettings(
        _env_file=None,
        paper_trade_executor_enabled=False,
    )

    await _start_paper_trade_executor(app, redis, settings)

    assert not hasattr(app.state, "paper_trade_executor_task")
    assert not hasattr(app.state, "paper_trade_http_client")
