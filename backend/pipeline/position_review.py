"""Position review — DeepSeek R1 primary, Mistral Large fallback, staged timeout exits."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Protocol

import httpx
from loguru import logger
from pydantic import BaseModel, Field

from atlas.shared.config import PolarisSettings
from backend.config.pipeline_config import (
    DEEPSEEK_REVIEW_TIMEOUT_S,
    MISTRAL_REVIEW_TIMEOUT_S,
    PARTIAL_EXIT_PCT,
    REVIEW_FULL_EXIT_AT_S,
    REVIEW_PARTIAL_EXIT_AT_S,
)
from backend.pipeline.gate_score import GateChainResult
from backend.pipeline.llm_chat import (
    deepseek_chat_url,
    fetch_chat_json,
    mistral_chat_url,
    secret_or_empty,
)


class StagedExitGovernor(Protocol):
    """Executes partial or full closes via PROMETHEUS."""

    async def partial_close(
        self,
        *,
        order_id: str,
        symbol: str,
        direction: str,
        close_pct: float,
        reason: str,
    ) -> None:
        ...

    async def full_close(
        self,
        *,
        order_id: str,
        symbol: str,
        direction: str,
        reason: str,
    ) -> None:
        ...


class FillContext(BaseModel, frozen=True):
    """Filled position context passed to reviewers."""

    symbol: str
    direction: str
    entry_price: float
    position_size_usd: float
    leverage: int
    fill_timestamp: float
    gate_chain: GateChainResult
    order_id: str


class ReviewVerdict(BaseModel, frozen=True):
    verdict: str
    model: str
    confidence: float
    reason: str
    responded_at_seconds: float
    position_remaining_pct: float


REVIEW_SYSTEM_PROMPT = """You are POLARIS position auditor. A trade was just executed.
Decide HOLD or EXIT based on the gate chain and fill details.

RESPONSE FORMAT (JSON only):
{
  "verdict": "HOLD" | "EXIT",
  "confidence": 0.0-1.0,
  "reason": "one clear sentence",
  "risk_level": "low" | "medium" | "high"
}

EXIT if the thesis is invalidated. HOLD if signals still align. Valid JSON only."""


class PositionReviewer:
    """Staged review with T+30 partial exit and T+60 full exit."""

    def __init__(
        self,
        risk_governor: StagedExitGovernor,
        *,
        settings: PolarisSettings,
        http_client: httpx.AsyncClient,
        partial_exit_at_s: float = REVIEW_PARTIAL_EXIT_AT_S,
        full_exit_at_s: float = REVIEW_FULL_EXIT_AT_S,
    ) -> None:
        self._risk_governor = risk_governor
        self._settings = settings
        self._http = http_client
        self._partial_at = partial_exit_at_s
        self._full_at = full_exit_at_s

    async def review(self, fill: FillContext) -> ReviewVerdict:
        start = time.monotonic()
        logger.info(
            "position_review_start | symbol={} | direction={} | entry={}",
            fill.symbol,
            fill.direction,
            fill.entry_price,
        )

        deepseek_task = asyncio.create_task(
            self._request_review(fill, provider="deepseek"),
            name="deepseek_review_{}".format(fill.symbol),
        )

        position_remaining = 1.0
        mistral_task: asyncio.Task[dict[str, Any]] | None = None

        try:
            response = await asyncio.wait_for(
                asyncio.shield(deepseek_task),
                timeout=self._partial_at,
            )
            elapsed = time.monotonic() - start
            return await self._apply_verdict(
                response,
                fill,
                position_remaining,
                elapsed,
                "deepseek",
            )
        except asyncio.TimeoutError:
            elapsed_30 = time.monotonic() - start
            logger.warning(
                "position_review_partial_timeout | symbol={} | elapsed_s={:.0f}",
                fill.symbol,
                elapsed_30,
            )
            await self._risk_governor.partial_close(
                order_id=fill.order_id,
                symbol=fill.symbol,
                direction=fill.direction,
                close_pct=PARTIAL_EXIT_PCT,
                reason="DeepSeek review timeout at T+30s",
            )
            position_remaining = 1.0 - PARTIAL_EXIT_PCT
            mistral_task = asyncio.create_task(
                self._request_review(fill, provider="mistral"),
                name="mistral_review_{}".format(fill.symbol),
            )

        time_used = time.monotonic() - start
        remaining_window = self._full_at - time_used
        pending = [t for t in (deepseek_task, mistral_task) if t is not None and not t.done()]

        if pending and remaining_window > 0:
            try:
                done, still_pending = await asyncio.wait(
                    pending,
                    timeout=max(0.0, remaining_window),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if done:
                    completed = list(done)[0]
                    response = completed.result()
                    elapsed = time.monotonic() - start
                    model_name = "deepseek" if completed is deepseek_task else "mistral"
                    for task in still_pending:
                        task.cancel()
                    return await self._apply_verdict(
                        response,
                        fill,
                        position_remaining,
                        elapsed,
                        model_name,
                    )
            except Exception as exc:
                logger.error(
                    "position_review_wait_error | symbol={} | err={}",
                    fill.symbol,
                    str(exc),
                )

        for task in (deepseek_task, mistral_task):
            if task is not None and not task.done():
                task.cancel()

        elapsed_final = time.monotonic() - start
        if position_remaining > 0:
            await self._risk_governor.full_close(
                order_id=fill.order_id,
                symbol=fill.symbol,
                direction=fill.direction,
                reason="Position review timed out at T+{:.0f}s".format(elapsed_final),
            )

        return ReviewVerdict(
            verdict="TIMEOUT",
            model="timeout",
            confidence=0.0,
            reason="Both DeepSeek and Mistral failed within {:.0f}s".format(self._full_at),
            responded_at_seconds=elapsed_final,
            position_remaining_pct=0.0,
        )

    async def _request_review(
        self,
        fill: FillContext,
        *,
        provider: str,
    ) -> dict[str, Any]:
        elapsed_at_entry = time.time() - fill.fill_timestamp
        user_content = """OPEN POSITION:
Symbol: {symbol}
Direction: {direction}
Entry price: ${entry:,.4f}
Position size: ${size:,.0f} at {lev}x leverage
Time since entry: {elapsed:.0f}s

GATE CHAIN THAT TRIGGERED THIS TRADE:
{chain}

Review this position — HOLD or EXIT?""".format(
            symbol=fill.symbol,
            direction=fill.direction,
            entry=fill.entry_price,
            size=fill.position_size_usd,
            lev=fill.leverage,
            elapsed=elapsed_at_entry,
            chain=fill.gate_chain.to_prompt_context(),
        )

        if provider == "deepseek":
            url = deepseek_chat_url(self._settings)
            auth = secret_or_empty(self._settings.deepseek_api_key)
            model = self._settings.deepseek_reasoner_model
            timeout_s = DEEPSEEK_REVIEW_TIMEOUT_S
        else:
            url = mistral_chat_url()
            auth = secret_or_empty(self._settings.embed_api_key)
            model = "mistral-large-latest"
            timeout_s = MISTRAL_REVIEW_TIMEOUT_S

        if not auth:
            raise ValueError("{} API key not configured".format(provider))

        parsed = await fetch_chat_json(
            http_client=self._http,
            url=url,
            authorization=auth,
            model=model,
            system_prompt=REVIEW_SYSTEM_PROMPT,
            user_content=user_content,
            max_tokens=150,
            timeout_s=timeout_s,
        )
        if parsed.get("verdict") not in ("HOLD", "EXIT"):
            raise ValueError("Invalid verdict: {}".format(parsed.get("verdict")))
        logger.info(
            "position_review_verdict | provider={} | verdict={} | reason={}",
            provider,
            parsed.get("verdict"),
            parsed.get("reason", ""),
        )
        return parsed

    async def _apply_verdict(
        self,
        response: dict[str, Any],
        fill: FillContext,
        position_remaining: float,
        elapsed: float,
        model_name: str,
    ) -> ReviewVerdict:
        verdict = str(response.get("verdict", "EXIT"))

        if verdict == "EXIT" and position_remaining > 0:
            await self._risk_governor.full_close(
                order_id=fill.order_id,
                symbol=fill.symbol,
                direction=fill.direction,
                reason="{} review: {}".format(
                    model_name,
                    response.get("reason", "EXIT"),
                ),
            )
            position_remaining = 0.0
        elif verdict == "HOLD":
            logger.info(
                "position_review_hold | symbol={} | remaining_pct={}",
                fill.symbol,
                position_remaining,
            )

        return ReviewVerdict(
            verdict=verdict,
            model=model_name,
            confidence=float(response.get("confidence", 0.5)),
            reason=str(response.get("reason", "")),
            responded_at_seconds=elapsed,
            position_remaining_pct=position_remaining,
        )
