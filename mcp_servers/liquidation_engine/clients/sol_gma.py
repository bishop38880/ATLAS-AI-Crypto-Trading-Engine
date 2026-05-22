import asyncio
from decimal import Decimal
from typing import List
from solana.rpc.async_api import AsyncClient
from solders.pubkey import Pubkey

from ..models import ObligationState

class KaminoGmaClient:
    """
    OnChainIntelligenceAgent - Forced Seller Orderbook & Liquidation Cascades
    Solana Client using getMultipleAccounts to fetch Kamino Obligations.
    """

    def __init__(self, rpc_url: str):
        self.client = AsyncClient(rpc_url)

    async def fetch_whale_states(
        self, 
        obligation_addresses: List[str], 
        asset: str, 
        current_price: Decimal
    ) -> List[ObligationState]:
        """
        Fetches multiple obligation accounts in a single call.
        """
        pubkeys = [Pubkey.from_string(addr) for addr in obligation_addresses]
        # Explicit timeout for Solana RPC call
        response = await asyncio.wait_for(
            self.client.get_multiple_accounts(pubkeys),
            timeout=10.0
        )
        
        states = []
        if not response.value:
            return states

        for i, account in enumerate(response.value):
            if account:
                states.append(self._parse_kamino_obligation(
                    obligation_addresses[i], asset, account.data, current_price
                ))
        return states

    def _parse_kamino_obligation(
        self, 
        address: str, 
        asset: str, 
        data: bytes, 
        current_price: Decimal
    ) -> ObligationState:
        """
        Parses Kamino Obligation byte data.
        NOTE: Simplified parsing for demonstration; requires full layout for production.
        """
        # Placeholder: In production, use Borsh to decode data.
        # Kamino stores USD values in a specific fixed-point format.
        # For this tool, we simulate the extraction.
        
        # Mocking extraction of total collateral/debt USD from byte offsets
        # Real offsets: Collateral (~200), Debt (~250)
        collateral_usd = Decimal("1000000")  # Mock
        debt_usd = Decimal("700000")        # Mock
        threshold = Decimal("0.8")          # Mock
        hf = (collateral_usd * threshold) / debt_usd if debt_usd > 0 else Decimal("10")
        
        liquidation_price = (debt_usd / (collateral_usd / current_price * threshold)) if collateral_usd > 0 else Decimal("0")

        return ObligationState(
            address=address,
            protocol="Kamino",
            asset=asset,
            collateral_usd=collateral_usd,
            debt_usd=debt_usd,
            health_factor=hf,
            liquidation_price=liquidation_price
        )
