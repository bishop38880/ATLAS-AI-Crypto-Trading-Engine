"""Trade orchestrator — gate chain → LM Studio → PROMETHEUS → position review."""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

import httpx
import msgspec
from loguru import logger
from redis.asyncio import Redis

from atlas.shared.config import PolarisSettings
from backend.config.pipeline_config import (
    FILL_KEY_PREFIX,
    PROMETHEUS_EXECUTION_CHANNEL,
    get_position_tier,
)
from backend.pipeline.gate_chain import GateChain, GateScoringEngine
from backend.pipeline.lm_studio_decision import get_entry_decision
from backend.pipeline.position_review import FillContext, PositionReviewer
from backend.pipeline.staged_exit import PrometheusStagedExitBridge


class TradeOrchestrator:
    """Top-level signal-to-review coordinator."""

    def __init__(
        self,
        redis_client: Redis,
        scoring_engine: GateScoringEngine,
        *,
        settings: PolarisSettings,
        http_client: httpx.AsyncClient,
    ) -> None:
        self._redis = redis_client
        self._settings = settings
        self._http = http_client
        self._gate_chain = GateChain(redis_client, scoring_engine)
        exit_bridge = PrometheusStagedExitBridge(redis_client)
        self._reviewer = PositionReviewer(
            exit_bridge,
            settings=settings,
            http_client=http_client,
        )

    async def evaluate_and_trade(
        self,
        symbol: str,
        capital_usd: float = 5000.0,
    ) -> dict[str, Any]:
        """Run the full pipeline; return an audit dict for the trade journal."""
        run_id = str(uuid.uuid4())[:8]
        started = time.time()
        audit: dict[str, Any] = {
            "run_id": run_id,
            "symbol": symbol.upper(),
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "outcome": "unknown",
        }

        try:
            logger.info("orchestrator_gate_chain | run_id={} | symbol={}", run_id, symbol)
            chain_result = await self._gate_chain.evaluate(symbol)
            audit["gate_chain"] = _gate_chain_audit(chain_result)

            if not chain_result.passed:
                audit["outcome"] = "gate_fail:{}".format(chain_result.fail_at_gate)
                return audit

            logger.info("orchestrator_lm_studio | run_id={} | symbol={}", run_id, symbol)
            lm_decision = await get_entry_decision(
                chain_result,
                settings=self._settings,
                http_client=self._http,
                capital_usd=capital_usd,
            )
            audit["lm_decision"] = lm_decision

            if lm_decision is None or lm_decision.get("decision") == "SKIP":
                reason = (
                    lm_decision.get("primary_reason", "No response")
                    if lm_decision
                    else "Model timeout"
                )
                audit["outcome"] = "lm_skip:{}".format(reason)
                return audit

            decision = str(lm_decision["decision"])
            tier = get_position_tier(chain_result.final_score)
            if tier is None:
                audit["outcome"] = "score_below_threshold"
                return audit

            logger.info(
                "orchestrator_prometheus | run_id={} | symbol={} | direction={} | size_pct={}",
                run_id,
                symbol,
                decision,
                tier["size_pct"],
            )
            execution_request = {
                "run_id": run_id,
                "symbol": symbol.upper(),
                "direction": decision,
                "position_size_pct": tier["size_pct"],
                "leverage": tier["leverage"],
                "capital_usd": capital_usd,
                "score": chain_result.final_score,
                "gate_chain_summary": chain_result.to_prompt_context(),
            }
            await self._redis.publish(
                PROMETHEUS_EXECUTION_CHANNEL,
                msgspec.json.encode(execution_request).decode("utf-8"),
            )

            fill_data = await self._wait_for_fill(run_id, timeout=30.0)
            if fill_data is None:
                audit["outcome"] = "execution_timeout"
                return audit

            audit["fill"] = fill_data

            fill_context = FillContext(
                symbol=symbol.upper(),
                direction=decision,
                entry_price=float(fill_data["entry_price"]),
                position_size_usd=float(fill_data["position_size_usd"]),
                leverage=int(tier["leverage"]),
                fill_timestamp=float(fill_data["fill_timestamp"]),
                gate_chain=chain_result,
                order_id=str(fill_data["order_id"]),
            )

            logger.info("orchestrator_review | run_id={} | symbol={}", run_id, symbol)
            verdict = await self._reviewer.review(fill_context)
            audit["review"] = verdict.model_dump()
            audit["outcome"] = "reviewed:{}:{}".format(verdict.verdict, verdict.model)

            elapsed = round(time.time() - started, 2)
            logger.info(
                "orchestrator_complete | run_id={} | symbol={} | elapsed_s={} | verdict={}",
                run_id,
                symbol,
                elapsed,
                verdict.verdict,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("orchestrator_error | run_id={} | err={}", run_id, str(exc))
            audit["outcome"] = "error:{}".format(str(exc))

        return audit

    async def _wait_for_fill(self, run_id: str, timeout: float) -> dict[str, Any] | None:
        fill_key = "{}{}".format(FILL_KEY_PREFIX, run_id)
        deadline = time.time() + timeout

        while time.time() < deadline:
            raw = await self._redis.get(fill_key)
            if raw:
                await self._redis.delete(fill_key)
                return msgspec.json.decode(raw, type=dict)
            await asyncio.sleep(0.5)

        return None


def _gate_chain_audit(chain_result: Any) -> dict[str, Any]:
    return {
        "passed": chain_result.passed,
        "final_score": chain_result.final_score,
        "fail_at_gate": chain_result.fail_at_gate,
        "fail_reason": chain_result.fail_reason,
        "gates": {
            tf: {
                "score": gs.score,
                "direction": gs.direction.value,
                "regime": gs.regime.value,
                "passed": gs.passed,
            }
            for tf, gs in chain_result.gates.items()
        },
    }
