import asyncio
from decimal import Decimal
from typing import List, Dict
from web3 import AsyncWeb3
from web3.contract import AsyncContract
from eth_abi.abi import decode

from ..config import (
    AAVE_V3_POOL_DATA_PROVIDER,
    MULTICALL3_ADDRESS,
    AAVE_DATA_PROVIDER_ABI,
    MULTICALL3_ABI,
    BASE_CURRENCY_UNIT
)
from ..models import ObligationState

class AaveMulticallClient:
    """
    OnChainIntelligenceAgent - Forced Seller Orderbook & Liquidation Cascades
    EVM Client using Multicall3 to batch Aave V3 health checks.
    """
    
    def __init__(self, w3: AsyncWeb3):
        self.w3 = w3
        self.multicall = w3.eth.contract(
            address=MULTICALL3_ADDRESS, 
            abi=MULTICALL3_ABI
        )
        self.data_provider = w3.eth.contract(
            address=AAVE_V3_POOL_DATA_PROVIDER,
            abi=AAVE_DATA_PROVIDER_ABI
        )

    async def fetch_whale_states(
        self, 
        whale_addresses: List[str], 
        asset: str, 
        current_price: Decimal
    ) -> List[ObligationState]:
        """
        Batches getUserAccountData calls via Multicall3.
        """
        calls = []
        for whale in whale_addresses:
            data = self.data_provider.encode_abi("getUserAccountData", [whale])
            calls.append({"target": AAVE_V3_POOL_DATA_PROVIDER, "callData": data})
        
        # Explicit timeout for network call
        _, return_data = await asyncio.wait_for(
            self.multicall.functions.aggregate(calls).call(),
            timeout=10.0
        )
        
        states = []
        for i, raw_response in enumerate(return_data):
            decoded = decode(
                ["uint256", "uint256", "uint256", "uint256", "uint256", "uint256"], 
                raw_response
            )
            states.append(self._parse_aave_state(
                whale_addresses[i], asset, decoded, current_price
            ))
        return states

    def _parse_aave_state(
        self, 
        address: str, 
        asset: str, 
        data: tuple, 
        current_price: Decimal
    ) -> ObligationState:
        """
        Parses raw Aave data and calculates exact liquidation price.
        """
        # totalCollateralBase, totalDebtBase, _, currentLiquidationThreshold, _, healthFactor
        coll_base = Decimal(data[0]) / BASE_CURRENCY_UNIT
        debt_base = Decimal(data[1]) / BASE_CURRENCY_UNIT
        # threshold is in 4 decimals (e.g. 8000 = 0.8)
        threshold = Decimal(data[3]) / Decimal("10000")
        hf = Decimal(data[5]) / Decimal("10")**18

        # Trigger Price = Total Debt / (Collateral Amount * Liquidation Threshold)
        # Simplified: (Debt / (CollateralValue * Threshold)) * CurrentPrice
        if coll_base > 0 and threshold > 0:
            liquidation_price = (debt_base / (coll_base * threshold)) * current_price
        else:
            liquidation_price = Decimal("0")

        return ObligationState(
            address=address,
            protocol="Aave",
            asset=asset,
            collateral_usd=coll_base,
            debt_usd=debt_base,
            health_factor=hf,
            liquidation_price=liquidation_price
        )
