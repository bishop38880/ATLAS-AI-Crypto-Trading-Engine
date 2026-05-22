from fastapi import APIRouter, Request
from pydantic import BaseModel
from typing import List, Dict, Any
from redis.asyncio import Redis

from atlas.api._channel_reads import read_gnn

router = APIRouter(prefix="/api/gnn", tags=["gnn"])

class GNNShadowResponse(BaseModel):
    shadowScores: List[Any]
    walletAnalysis: Dict[str, Any]
    leadLag: Dict[str, Any]
    inferenceStats: Dict[str, Any]

@router.get("/shadow", response_model=GNNShadowResponse)
async def get_gnn_shadow(request: Request) -> GNNShadowResponse:
    redis: Redis = request.app.state.redis
    data = await read_gnn(redis)
    return GNNShadowResponse(
        shadowScores=data["shadowScores"],
        walletAnalysis=data["walletAnalysis"],
        leadLag=data["leadLag"],
        inferenceStats=data["inferenceStats"]
    )
