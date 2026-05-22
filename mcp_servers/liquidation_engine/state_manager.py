import asyncio
from decimal import Decimal
from typing import Dict, List, Optional
from loguru import logger

from .models import ObligationState, LiquidationCluster
from .config import EVM_WHALES, SOL_OBLIGATIONS

class LiquidationStateManager:
    """
    OnChainIntelligenceAgent - Forced Seller Orderbook & Liquidation Cascades
    Maintains in-memory state of DeFi whale health and liquidation clusters.
    """

    def __init__(self):
        # Maps protocol -> asset -> list of obligations
        self.states: Dict[str, Dict[str, List[ObligationState]]] = {
            "Aave": {},
            "Kamino": {}
        }
        # Maps asset -> list of clusters
        self.liquidation_map: Dict[str, List[LiquidationCluster]] = {}
        self.is_running = False

    async def start_sync_loop(self, evm_client, sol_client, interval: int = 30):
        """
        Background loop to refresh whale data and re-calculate clusters.
        """
        self.is_running = True
        while self.is_running:
            try:
                # 1. Fetch current prices (Stubbed for now, in prod use Pyth/Chainlink)
                eth_price = Decimal("2500")
                sol_price = Decimal("150")

                # Concurrent fetch for multi-chain liquidity health
                aave_task = evm_client.fetch_whale_states(EVM_WHALES, "ETH", eth_price)
                sol_task = sol_client.fetch_whale_states(SOL_OBLIGATIONS, "SOL", sol_price)
                
                aave_results, sol_results = await asyncio.gather(aave_task, sol_task)
                
                self.states["Aave"]["ETH"] = aave_results
                self.states["Kamino"]["SOL"] = sol_results

                # 4. Rebuild Liquidation Map
                self._rebuild_map("ETH", eth_price)
                self._rebuild_map("SOL", sol_price)

                logger.info("Liquidation state synced successfully.")
            except asyncio.CancelledError:
                logger.info("Liquidation sync loop cancelled.")
                raise
            except Exception as e:
                logger.error("Error in liquidation sync loop | error={}", e)
            
            if interval == 0:  # For testing
                break
            await asyncio.sleep(interval)

    def _rebuild_map(self, asset: str, current_price: Decimal):
        """
        Clusters trigger prices into 1% bands.
        """
        all_obligations = []
        for protocol in self.states:
            all_obligations.extend(self.states[protocol].get(asset, []))

        if not all_obligations:
            return

        # Define 1% bands (up to 20% drop)
        band_size = current_price * Decimal("0.01")
        clusters = []

        for i in range(20):
            upper = current_price - (Decimal(str(i)) * band_size)
            lower = current_price - (Decimal(str(i + 1)) * band_size)
            
            # Filter obligations in this band
            in_band = [o for o in all_obligations if lower <= o.liquidation_price < upper]
            
            if in_band:
                clusters.append(LiquidationCluster(
                    price_band_lower=lower,
                    price_band_upper=upper,
                    total_collateral_to_liquidate_usd=sum((o.collateral_usd for o in in_band), Decimal("0")),
                    whale_count=len(in_band)
                ))
        
        self.liquidation_map[asset] = clusters

    def get_clusters(self, asset: str) -> List[LiquidationCluster]:
        return self.liquidation_map.get(asset, [])

    def get_distressed_whales(self, protocol: str, asset: str, limit: int = 10) -> List[ObligationState]:
        whales = self.states.get(protocol, {}).get(asset, [])
        return sorted(whales, key=lambda x: x.health_factor)[:limit]
