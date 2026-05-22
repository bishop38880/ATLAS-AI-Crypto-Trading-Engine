"""REST surface for perpetual funding diagnostics."""

from __future__ import annotations

from fastapi import APIRouter, Request

from atlas.api.funding_command_center import (
    FundingCommandCenterPayload,
    fetch_funding_command_center_snapshot,
)

router = APIRouter(prefix="/api/funding", tags=["funding"])


@router.get("/command-center", response_model=FundingCommandCenterPayload, response_model_by_alias=True)
async def get_funding_command_center(request: Request) -> FundingCommandCenterPayload:
    """Aggregated OKX MCP cache funding ladder for the POLARIS 33-asset horizon."""

    redis = request.app.state.redis
    return await fetch_funding_command_center_snapshot(redis)
