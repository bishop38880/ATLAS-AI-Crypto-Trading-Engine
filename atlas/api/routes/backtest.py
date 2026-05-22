"""REST surface for the dashboard DuckDB backtest suite."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, HTTPException, Query, status
from loguru import logger

from atlas.api.backtest_dashboard_logic import (
    fetch_backtest_inventory,
    fetch_dashboard_backtest_run,
    list_dashboard_backtest_runs,
    run_dashboard_backtest,
    seed_demo_backtest_fixtures,
)
from atlas.api.schemas import (
    BacktestInventoryPayload,
    BacktestRunCreatedPayload,
    BacktestRunDetailPayload,
    BacktestRunListPayload,
    BacktestRunRequestBody,
    BacktestSeedPayload,
)
from atlas.shared.config import PolarisSettings

router = APIRouter(prefix="/api/backtest", tags=["backtest"])


def _settings() -> PolarisSettings:
    return PolarisSettings()


def _parse_capital(raw: str) -> Decimal:
    try:
        value = Decimal(raw.strip())
    except InvalidOperation as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="invalid_initial_capital",
        ) from exc
    if value <= Decimal("0"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="initial_capital_must_be_positive",
        )
    return value


@router.get("/inventory", response_model=BacktestInventoryPayload, response_model_by_alias=True)
async def get_backtest_inventory() -> BacktestInventoryPayload:
    """Row counts and distinct assets in the DuckDB replay warehouse."""
    return await fetch_backtest_inventory(_settings())


@router.get("/runs", response_model=BacktestRunListPayload, response_model_by_alias=True)
async def get_backtest_runs(
    limit: int = Query(default=25, ge=1, le=100),
) -> BacktestRunListPayload:
    """Recent persisted backtest runs newest-first."""
    return await list_dashboard_backtest_runs(_settings(), limit=limit)


@router.get("/runs/{run_id}", response_model=BacktestRunDetailPayload, response_model_by_alias=True)
async def get_backtest_run_detail(run_id: str) -> BacktestRunDetailPayload:
    """Metrics, equity curve, and trades for one completed run."""
    detail = await fetch_dashboard_backtest_run(_settings(), run_id)
    if detail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="backtest_run_not_found")
    return detail


@router.post("/seed-demo", response_model=BacktestSeedPayload, response_model_by_alias=True)
async def post_backtest_seed_demo() -> BacktestSeedPayload:
    """Load bundled smoke candles + signals for first-time dashboard use."""
    return await seed_demo_backtest_fixtures(_settings())


@router.post("/run", response_model=BacktestRunCreatedPayload, response_model_by_alias=True)
async def post_backtest_run(body: BacktestRunRequestBody) -> BacktestRunCreatedPayload:
    """Replay imported candles and signals for the requested UTC window."""
    capital = _parse_capital(body.initial_capital_usd)
    try:
        return await run_dashboard_backtest(
            _settings(),
            asset=body.asset.strip().upper(),
            timeframe=body.timeframe.strip(),
            start_day=body.start_day.strip(),
            end_day=body.end_day.strip(),
            initial_capital_usd=capital,
            score_threshold=body.score_threshold,
        )
    except ValueError as exc:
        logger.warning("backtest_run_rejected | reason={}", exc)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
