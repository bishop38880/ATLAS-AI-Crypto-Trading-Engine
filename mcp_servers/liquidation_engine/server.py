import asyncio
import os
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from typing import List, Optional, Any
from decimal import Decimal
from mcp.server.fastmcp import FastMCP
from web3 import AsyncWeb3
from dotenv import load_dotenv
from loguru import logger

from .config import EVM_WHALES, SOL_OBLIGATIONS
from .models import ObligationState, LiquidationCluster, CascadeRiskReport
from .clients.evm_multicall import AaveMulticallClient
from .clients.sol_gma import KaminoGmaClient
from .state_manager import LiquidationStateManager

# OnChainIntelligenceAgent - Forced Seller Orderbook & Liquidation Cascades
# FastMCP Server implementation

load_dotenv()

state_manager = LiquidationStateManager()

@asynccontextmanager
async def _lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
    """
    Initializes clients and launches the background sync task.
    """
    eth_rpc = os.getenv("ETH_RPC_URL")
    sol_rpc = os.getenv("SOL_RPC_URL")

    if not eth_rpc or not sol_rpc:
        logger.warning("Missing RPC URLs in environment. Sync loop will fail.")
        # We yield anyway to allow the server to start, tools will return empty
        yield {}
        return

    w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(eth_rpc))
    evm_client = AaveMulticallClient(w3)
    sol_client = KaminoGmaClient(sol_rpc)

    # Launch background sync
    sync_task = asyncio.create_task(state_manager.start_sync_loop(evm_client, sol_client))
    
    try:
        yield {}
    finally:
        state_manager.is_running = False
        sync_task.cancel()
        try:
            await sync_task
        except asyncio.CancelledError:
            pass

mcp = FastMCP(
    "Liquidation_Engine",
    lifespan=_lifespan,
    instructions="Tracks Aave and Kamino whale liquidations for wick-catching alpha."
)

@mcp.tool()
async def get_whale_health_factors(protocol: str, asset: str) -> List[ObligationState]:
    """
    Returns the top 10 most distressed large wallets on the requested protocol.
    Includes exact debt size and estimated trigger price.
    """
    return state_manager.get_distressed_whales(protocol, asset)

@mcp.tool()
async def get_liquidation_map(asset: str) -> List[LiquidationCluster]:
    """
    Returns the aggregated 'Forced Seller Orderbook' for an asset.
    Shows price bands and total USD value of collateral poised for liquidation.
    """
    return state_manager.get_clusters(asset)

@mcp.tool()
async def evaluate_cascade_risk(
    asset: str, 
    current_price: float, 
    drop_simulations: List[float] = [0.05, 0.10, 0.15]
) -> List[CascadeRiskReport]:
    """
    The Master Alpha Tool. Simulates market drops and calculates forced selling volume.
    Returns optimal limit bid prices to 'catch the wick'.
    """
    c_price = Decimal(str(current_price))
    clusters = state_manager.get_clusters(asset)
    reports = []

    for drop in drop_simulations:
        drop_dec = Decimal(str(drop))
        target_price = c_price * (Decimal("1") - drop_dec)
        
        # Calculate volume to be liquidated above target_price
        selling_volume = sum(
            c.total_collateral_to_liquidate_usd 
            for c in clusters 
            if c.price_band_lower >= target_price
        )

        catch_wick = selling_volume > Decimal("50000000")  # $50M threshold
        risk = "HIGH" if selling_volume > Decimal("100000000") else "MEDIUM" if catch_wick else "LOW"
        
        reports.append(CascadeRiskReport(
            asset=asset,
            current_price=c_price,
            simulated_drop_pct=drop_dec * 100,
            forced_selling_volume_usd=Decimal(str(selling_volume)),
            catch_the_wick=catch_wick,
            optimal_bid_price=target_price * Decimal("0.99") if catch_wick else None,
            risk_level=risk
        ))
    
    return reports

if __name__ == "__main__":
    mcp.run()
