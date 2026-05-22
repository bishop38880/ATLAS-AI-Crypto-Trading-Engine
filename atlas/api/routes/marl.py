from fastapi import APIRouter
from typing import Dict, Any

router = APIRouter(prefix="/api/marl", tags=["marl"])

@router.get("/latest")
async def get_marl_latest(asset: str) -> Dict[str, Any]:
    return {
        "asset": asset,
        "enabled": False,
        "shadowMode": True,
        "deliberation": None,
    }
