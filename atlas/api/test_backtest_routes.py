"""Tests for GET/POST /api/backtest dashboard surface."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from atlas.api.routes.backtest import router as backtest_router


@pytest.fixture()
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    duck_path = tmp_path / "dashboard_bt.duckdb"
    monkeypatch.setenv("ATLAS_BACKTEST_DUCKDB_PATH", str(duck_path))
    _app = FastAPI()
    _app.include_router(backtest_router)
    return _app


@pytest.fixture()
async def client(app: FastAPI) -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client


@pytest.mark.anyio
async def test_backtest_inventory_empty_warehouse(client: AsyncClient) -> None:
    response = await client.get("/api/backtest/inventory")
    assert response.status_code == 200
    body = response.json()
    assert body["candleCount"] == 0
    assert body["signalCount"] == 0
    assert body["runCount"] == 0


@pytest.mark.anyio
async def test_backtest_seed_demo_and_run(client: AsyncClient) -> None:
    seed = await client.post("/api/backtest/seed-demo")
    assert seed.status_code == 200
    seed_body = seed.json()
    assert seed_body["candlesImported"] > 0
    assert seed_body["signalsImported"] > 0

    inventory = await client.get("/api/backtest/inventory")
    assert inventory.json()["candleCount"] > 0

    run_resp = await client.post(
        "/api/backtest/run",
        json={
            "asset": "BTCUSDT",
            "timeframe": "1h",
            "startDay": "2024-01-01",
            "endDay": "2024-01-15",
            "initialCapitalUsd": "10000",
            "scoreThreshold": 65.0,
        },
    )
    assert run_resp.status_code == 200
    run_body = run_resp.json()
    run_id = run_body["runId"]
    assert run_body["metrics"]["totalTrades"] >= 0

    list_resp = await client.get("/api/backtest/runs")
    assert list_resp.status_code == 200
    runs = list_resp.json()["runs"]
    assert any(row["runId"] == run_id for row in runs)

    detail_resp = await client.get(f"/api/backtest/runs/{run_id}")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["metrics"]["runId"] == run_id


@pytest.mark.anyio
async def test_backtest_run_detail_404(client: AsyncClient) -> None:
    response = await client.get("/api/backtest/runs/does-not-exist")
    assert response.status_code == 404
