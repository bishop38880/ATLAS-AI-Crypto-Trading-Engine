"""LM Studio entry decision after the gate chain passes."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from loguru import logger

from atlas.shared.config import PolarisSettings
from backend.config.pipeline_config import LM_STUDIO_TIMEOUT_S, get_position_tier
from backend.pipeline.gate_score import GateChainResult
from backend.pipeline.llm_chat import (
    fetch_chat_json,
    lmstudio_bearer,
    lmstudio_chat_url,
)

ENTRY_DECISION_SYSTEM_PROMPT = """You are POLARIS, an autonomous crypto trading decision engine.

You receive pre-computed multi-timeframe confluence gate scores for a single asset.
Your job: decide whether to enter a LONG, SHORT, or SKIP based on the gate chain.

SCORING FRAMEWORK (220 points total):
- Derivatives Intelligence: 75pts
- Whale/On-Chain Activity: 65pts
- Social Sentiment: 35pts (gated ~90% of the time)
- Macro Context: 30pts
- Technical Structure: 15pts

POSITION TIERS:
- Score 180+: 5% capital, 5x leverage
- Score 150-179: 3% capital, 3x leverage
- Score 120-149: 2% capital, 2x leverage
- Below 120: SKIP

RESPONSE FORMAT (JSON only, no preamble):
{
  "decision": "LONG" | "SHORT" | "SKIP",
  "confidence": 0.0-1.0,
  "position_size_pct": 2.0 | 3.0 | 5.0 | 0,
  "leverage": 2 | 3 | 5 | 0,
  "primary_reason": "one sentence",
  "risk_flags": ["list", "any", "concerns"]
}

Rules:
- SKIP if any risk flag is critical
- SKIP if confidence < 0.55
- Never invent data not in the gate chain
- Never output anything except valid JSON"""


def _resolve_lmstudio_model(settings: PolarisSettings) -> str:
    for candidate in (
        settings.omnibox_chat_model.strip(),
        settings.router_local_model.strip(),
        settings.local_model_name.strip(),
    ):
        if candidate:
            return candidate
    return "local-model"


async def get_entry_decision(
    chain_result: GateChainResult,
    *,
    settings: PolarisSettings,
    http_client: httpx.AsyncClient,
    capital_usd: float = 5000.0,
) -> dict[str, Any] | None:
    """Request LONG / SHORT / SKIP from LM Studio."""
    if not chain_result.passed:
        return {
            "decision": "SKIP",
            "confidence": 1.0,
            "primary_reason": "Gate chain failed",
        }

    tier = get_position_tier(chain_result.final_score)
    if tier is None:
        return {
            "decision": "SKIP",
            "confidence": 1.0,
            "primary_reason": "Score below minimum threshold",
        }

    user_content = """ASSET: {symbol}
FINAL SCORE: {score}/220
DIRECTION: {direction}
TIMEFRAME BONUS: {bonus:+d}pts
CAPITAL: ${capital:,.0f}

{context}

ELIGIBLE POSITION: {size}% (${notional:,.0f}) at {lev}x leverage

Make your entry decision:""".format(
        symbol=chain_result.symbol,
        score=chain_result.final_score,
        direction=chain_result.final_direction.value,
        bonus=chain_result.timeframe_bonus,
        capital=capital_usd,
        context=chain_result.to_prompt_context(),
        size=tier["size_pct"],
        notional=capital_usd * float(tier["size_pct"]) / 100.0,
        lev=tier["leverage"],
    )

    model = _resolve_lmstudio_model(settings)
    try:
        decision = await fetch_chat_json(
            http_client=http_client,
            url=lmstudio_chat_url(settings),
            authorization=lmstudio_bearer(settings),
            model=model,
            system_prompt=ENTRY_DECISION_SYSTEM_PROMPT,
            user_content=user_content,
            max_tokens=200,
            timeout_s=LM_STUDIO_TIMEOUT_S,
        )
    except asyncio.CancelledError:
        raise
    except TimeoutError:
        logger.error(
            "lm_studio_timeout | symbol={} | timeout_s={}",
            chain_result.symbol,
            LM_STUDIO_TIMEOUT_S,
        )
        return None
    except Exception as exc:
        logger.error(
            "lm_studio_error | symbol={} | err={}",
            chain_result.symbol,
            str(exc),
        )
        return None

    if "decision" not in decision:
        logger.error("lm_studio_missing_decision | symbol={}", chain_result.symbol)
        return None
    if decision["decision"] not in ("LONG", "SHORT", "SKIP"):
        logger.error(
            "lm_studio_invalid_decision | symbol={} | value={}",
            chain_result.symbol,
            decision["decision"],
        )
        return None

    logger.info(
        "lm_studio_decision | symbol={} | decision={} | confidence={}",
        chain_result.symbol,
        decision["decision"],
        decision.get("confidence", 0),
    )
    return decision
