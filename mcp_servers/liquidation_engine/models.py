from decimal import Decimal
from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional

# OnChainIntelligenceAgent - Forced Seller Orderbook & Liquidation Cascades
# Data Models with strict Decimal precision and frozen state.

class ObligationState(BaseModel):
    """
    Represents the health and position of a DeFi whale.
    """
    model_config = ConfigDict(frozen=True)

    address: str
    protocol: str  # "Aave" or "Kamino"
    asset: str     # e.g., "ETH", "SOL"
    collateral_usd: Decimal
    debt_usd: Decimal
    health_factor: Decimal
    liquidation_price: Decimal
    
    @property
    def is_distressed(self) -> bool:
        return self.health_factor < Decimal("1.1")

class LiquidationCluster(BaseModel):
    """
    Aggregates liquidation volume at a specific price level.
    """
    model_config = ConfigDict(frozen=True)

    price_band_lower: Decimal
    price_band_upper: Decimal
    total_collateral_to_liquidate_usd: Decimal
    whale_count: int

class CascadeRiskReport(BaseModel):
    """
    Simulation of forced selling cascades.
    """
    model_config = ConfigDict(frozen=True)

    asset: str
    current_price: Decimal
    simulated_drop_pct: Decimal
    forced_selling_volume_usd: Decimal
    catch_the_wick: bool = False
    optimal_bid_price: Optional[Decimal] = None
    risk_level: str = "LOW"  # LOW, MEDIUM, HIGH, SYSTEMIC
