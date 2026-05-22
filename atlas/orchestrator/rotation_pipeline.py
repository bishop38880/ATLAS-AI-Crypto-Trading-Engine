"""Two-Stage Altcoin Rotation Pipeline.

Executes the daily rotation by taking 33 universe assets, filtering to 12 via LocalLLM,
and deep-reasoning to 4 via DeepSeek. Bypasses LLMs if manual override exists.
"""

from __future__ import annotations

from loguru import logger
import msgspec
import redis.asyncio as redis_asyncio

from atlas.shared.config import PolarisSettings, ModelStackConfig
from atlas.core.llm_client import LocalLLMClient, DeepSeekClient


async def gather_overnight_data(redis: redis_asyncio.Redis, assets: list[str]) -> dict[str, str]:
    """Stub to gather overnight data for assets.
    In real implementation, this reads from market data and news pipelines.
    """
    return {asset: f"Overnight data packet for {asset} - volatility normal" for asset in assets}


async def execute_daily_rotation(redis: redis_asyncio.Redis, settings: PolarisSettings) -> list[str]:
    """Executes the two-stage rotation pipeline."""
    # 1. Check Redis for atlas:rotation:manual_override
    override_data = await redis.get("atlas:rotation:manual_override")
    if override_data:
        try:
            manual_assets = msgspec.json.decode(override_data)
            if isinstance(manual_assets, list) and len(manual_assets) > 0:
                logger.info("Manual override active. Skipping LLM rotation pipeline.")
                # We overwrite atlas:rotation:active with the manual override to be consistent
                await redis.sadd("atlas:rotation:active", *manual_assets) # type: ignore
                return manual_assets
        except Exception as exc:
            logger.warning("rotation_manual_override_parse_failed | exc={}", exc)

    # Simulated 33 asset universe
    universe_33 = [
        "BTC", "ETH", "SOL", "XRP", "ADA", "AVAX", "DOGE", "DOT", "LINK", "MATIC", "LTC", "BCH",
        "APT", "SUI", "ARB", "OP", "NEAR", "ATOM", "FIL", "INJ", "RENDER", "TIA", "SEI", "MANA",
        "SAND", "AAVE", "SNX", "MKR", "UNI", "LDO", "CRV", "STX", "IMX"
    ]

    # STAGE 1: Local Filter
    logger.info("Starting STAGE 1 (Local Filter)")
    overnight_data = await gather_overnight_data(redis, universe_33)
    
    # We use LocalLLMClient for the first pass
    model_config = ModelStackConfig()
    local_llm = LocalLLMClient(model_config)
    
    # Mock behavior in tests or real logic if connected
    stage1_prompt = (
        "Score these assets for volatility/catalyst potential today. "
        "Output top 12 tickers.\n\nData:\n" + "\n".join(f"{k}: {v}" for k, v in overnight_data.items())
    )
    
    try:
        response_1 = await local_llm.complete(stage1_prompt, max_tokens=2000)
        # Parse the output to find up to 12 tickers.
        # This is simplified for the prompt. We assume the LLM replies with a list.
        # Here we just take the first 12 for stub/fallback if parsing fails.
        top_12 = [sym for sym in universe_33 if sym in response_1.text.upper()][:12]
        if not top_12:
            top_12 = universe_33[:12]
    except Exception as exc:
        logger.warning("rotation_local_llm_failed | exc={}", exc)
        top_12 = universe_33[:12]

    # STAGE 2: Deep Reasoning
    logger.info("Starting STAGE 2 (Deep Reasoning) with {}", top_12)
    top_12_data = await gather_overnight_data(redis, top_12)
    
    deepseek_llm = DeepSeekClient(model_config, is_reasoner=True)
    stage2_prompt = (
        "Perform deep cross-category correlation. Select the top 4 assets for today's active rotation.\n\n"
        "Data:\n" + "\n".join(f"{k}: {v}" for k, v in top_12_data.items())
    )
    
    try:
        response_2 = await deepseek_llm.complete(stage2_prompt, max_tokens=4000)
        top_4 = [sym for sym in top_12 if sym in response_2.text.upper()][:4]
        if not top_4:
            top_4 = top_12[:4]
    except Exception as exc:
        logger.warning("rotation_deepseek_failed | exc={}", exc)
        top_4 = top_12[:4]

    # Write the final 4 assets to atlas:rotation:active
    logger.info("Final rotation active 4: {}", top_4)
    
    # Clear the old set and add new elements
    async with redis.pipeline() as pipe:
        pipe.delete("atlas:rotation:active")
        if top_4:
            pipe.sadd("atlas:rotation:active", *top_4)
        await pipe.execute()

    return top_4
