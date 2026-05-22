from decimal import Decimal
from pydantic import BaseModel, Field


class HypertrackerSnapshot(BaseModel):
    """Hyperliquid liquidation and drawdown snapshot."""

    model_config = {"frozen": True}

    estimated_liquidation_volume: Decimal = Field(default=Decimal("0"))
    hlp_vault_drawdown: Decimal = Field(default=Decimal("0"))
    status: str = Field(default="healthy")
