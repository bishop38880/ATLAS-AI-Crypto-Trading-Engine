from __future__ import annotations

from typing import List

from fastapi import APIRouter, Request

from atlas.api.routes.decision_journal import get_decision_journal
from atlas.api.routes.signals import get_signal_detail, get_signal_feed
from atlas.api.schemas import DecisionJournalResponse, SignalDetailPayload, SignalFeedEntry


router = APIRouter(prefix="/api/decisions", tags=["decisions"])

router.get(
    "/journal",
    response_model=DecisionJournalResponse,
    response_model_by_alias=True,
)(get_decision_journal)


@router.get("", response_model=List[SignalFeedEntry], response_model_by_alias=True)
async def get_decisions(request: Request) -> List[SignalFeedEntry]:
    """Compatibility alias for the POLARIS signal feed."""
    return await get_signal_feed(request)


@router.get("/{asset}", response_model=SignalDetailPayload, response_model_by_alias=True)
async def get_decision_detail(request: Request, asset: str) -> SignalDetailPayload:
    """Compatibility alias for per-asset signal detail."""
    return await get_signal_detail(request, asset)
