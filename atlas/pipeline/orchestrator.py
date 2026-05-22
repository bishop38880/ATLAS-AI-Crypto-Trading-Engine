"""PipelineOrchestrator — Coordinates agent execution and confluence scoring.

Uses async-native execution with a fast-path risk veto and a strict 80ms budget.
If the Safety Layer (RiskAgent) detects a severe cascade and vetoes, the orchestrator
short-circuits instantly. Otherwise, it enforces a quorum constraint where at least
3 agent categories must successfully respond within the budget.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, cast
from uuid import uuid4

import msgspec
import redis.asyncio as redis_async
from loguru import logger
from qdrant_client.models import FieldCondition, Filter, MatchValue

from atlas.api.schemas import AgentStatusRedis
from atlas.agents.base import (
    AgentCategory,
    AgentResult,
    AgentState,
    AgentTelemetry,
    BaseAgent,
)
from atlas.agents.synthesiser.synthesiser_agent import SynthesiserAgent
from atlas.core.monitoring_telemetry import (
    record_monitoring_alert,
    record_pipeline_cycle,
    safe_monitoring_write,
)
from atlas.core.risk_veto_journal import append_risk_veto_event
from atlas.core.consistency_checker import ConsistencyChecker
from atlas.core.cross_source_spot_price import spot_price_consistency_pyth_coingecko
from atlas.core.registry import get_provider
from atlas.ml.confidence_gate import PipelineConfidenceCalculator, PipelineConfidenceInputs
from atlas.llm.synthesis_evaluator import parse_synthesis_or_fallback
from atlas.models.signal import ConfidenceTier, SignalDecision, SignalDirection, SignalOutput
from atlas.models.signal import build_safe_fallback_decision
from atlas.schemas.synthesis_output import SynthesisOutput
from atlas.pipeline.execution_gate import log_skipped_opportunity
from atlas.pipeline.query_classifier import QueryClassification
from atlas.pipeline.reflection import ReflectionCritic
from atlas.pipeline.simple_signals_bridge import (
    build_all_signals_from_market_payload,
    build_all_signals_from_market_payload_async,
)
from backend.services.coinbase_premium.signals import fetch_premium_scoring_inputs
from atlas.rag.query import RAGQueryEngine, RetrievalDepth, ScoredDocument
from atlas.rag.retrieval_telemetry import append_retrieval_event, build_retrieval_event_dict
from atlas.scoring.confluence import ConfluenceScoringEngine, RegimeContext
from atlas.shared.coingecko_symbol_map import (
    COINGECKO_SIMPLE_PRICE_ID_BY_BASE,
    GECKO_TERMINAL_TOKEN_REF_BY_BASE,
)
from atlas.shared.config import PolarisSettings

_AGENT_STATUS_TTL_S: int = 600
_PIPELINE_EVENT_BUFFER_LENGTH: int = 100

# Map orchestrator agent.name (short id) → dashboard / confluence public name
_AGENT_REDIS_DISPLAY_NAME: dict[str, str] = {
    "derivatives": "DerivativesAgent",
    "options_intelligence_agent": "OptionsIntelligenceAgent",
    "onchain": "WhaleWatcherAgent",
    "technical": "TechnicalAgent",
    "sentiment": "SocialAgent",
    "regime": "MacroAgent",
    "risk": "risk",
}

_SIMPLE_MODE_BREAKDOWN_FOR_AGENT: dict[str, str] = {
    "derivatives": "derivatives",
    "onchain": "onchain",
    "technical": "technical",
    "sentiment": "sentiment",
    "regime": "market_context",
}


class PipelineOrchestrator:
    """Coordinates parallel agent execution with fast-path veto and 80ms budget."""

    QUORUM_MIN_CATEGORIES = 3

    def __init__(
        self,
        agents: list[BaseAgent],
        synthesiser: SynthesiserAgent,
        settings: PolarisSettings,
        redis_client: redis_async.Redis,  # type: ignore[type-arg]
        rag_engine: RAGQueryEngine | None = None,
        reflection_critic: ReflectionCritic | None = None,
        deepseek_client: Any | None = None,
    ) -> None:
        """Initialize the orchestrator.

        Args:
            agents: List of agent instances to execute.
            synthesiser: Tier 3 SynthesiserAgent.
            settings: PolarisSettings instance.
            redis_client: Injected shared Redis connection pool.
            rag_engine: Optional RAG query engine for context retrieval.
        """
        self._agents = agents
        self._synthesiser = synthesiser
        self._settings = settings
        self._redis = redis_client
        self._rag_engine = rag_engine
        self._reflection_critic = reflection_critic
        self._deepseek_client = deepseek_client
        self._last_rag_error: str | None = (
            None
            if rag_engine is not None
            else "RAG query engine unavailable: Qdrant/LanceDB startup check failed"
        )

    def _agent_redis_display_name(self, agent: BaseAgent) -> str | None:
        """Redis health key segment for dashboards (``agent:{name}:status``)."""
        return _AGENT_REDIS_DISPLAY_NAME.get(agent.name)

    def _monitoring_latency_target_ms(self) -> int:
        """Best-effort latency target used by the monitoring dashboard."""
        configured = getattr(self._settings, "pipeline_latency_target_ms", 10_000)
        try:
            return int(configured)
        except (TypeError, ValueError):
            return 10_000

    def _derive_agent_health_status(
        self, agent: BaseAgent, result: AgentResult
    ) -> tuple[str, int]:
        """Map agent runtime + result to AgentStatusRedis fields."""
        last_ping_ms = max(0, int(result.telemetry.latency_ms))

        if agent.state == AgentState.WARMING_UP or "AGENT_WARMING_UP" in result.risks:
            return "WARMING_UP", last_ping_ms
        if agent.state == AgentState.FAILED:
            return "FAILED", last_ping_ms
        if result.explanation.startswith("Timed out"):
            return "DEGRADED", last_ping_ms
        if result.explanation.startswith("Exception:"):
            return "FAILED", last_ping_ms
        if agent.state == AgentState.DEGRADED:
            return "DEGRADED", last_ping_ms
        if agent.category == AgentCategory.RISK and result.veto:
            return "READY", last_ping_ms
        return "READY", last_ping_ms

    async def _persist_agent_status(self, agent: BaseAgent, result: AgentResult) -> None:
        """Write per-agent health to Redis for /ws/agents and monitoring."""
        display = self._agent_redis_display_name(agent)
        if display is None:
            return

        status, last_ping_ms = self._derive_agent_health_status(agent, result)
        payload = AgentStatusRedis.model_validate(
            {
                "status": status,
                "lastPingMs": last_ping_ms,
                "lastScore": float(result.score),
                "maxPoints": float(result.max_score),
                "direction": result.direction.value,
                "explanation": result.explanation,
            }
        )
        key = f"agent:{display}:status"
        try:
            encoded = msgspec.json.encode(payload.model_dump(by_alias=True))
            await asyncio.wait_for(
                self._redis.setex(key, _AGENT_STATUS_TTL_S, encoded),
                timeout=2.0,
            )
            if status in ("FAILED", "DEGRADED"):
                level = "ERROR" if status == "FAILED" else "WARN"
                await safe_monitoring_write(
                    record_monitoring_alert(
                        self._redis,
                        level=level,
                        message=f"Agent '{display}' is in {status} state",
                        source="agent-health",
                        alert_id=f"{display}:{status}",
                    ),
                    action="record_agent_health_alert",
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "agent_status_redis_write_failed | key={} | error={}",
                key,
                str(exc),
            )

    async def _persist_cancelled_agent_placeholder(self, agent: BaseAgent) -> None:
        """Mark agents skipped (e.g. risk veto) as degraded for dashboards."""
        display = self._agent_redis_display_name(agent)
        if display is None:
            return
        payload = AgentStatusRedis.model_validate(
            {
                "status": "DEGRADED",
                "lastPingMs": 0,
                "lastScore": 0.0,
                "maxPoints": 0.0,
                "direction": "neutral",
                "explanation": "Skipped after risk veto.",
            }
        )
        key = f"agent:{display}:status"
        try:
            encoded = msgspec.json.encode(payload.model_dump(by_alias=True))
            await asyncio.wait_for(
                self._redis.setex(key, _AGENT_STATUS_TTL_S, encoded),
                timeout=2.0,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "agent_cancel_status_redis_write_failed | key={} | error={}",
                key,
                str(exc),
            )

    def _get_risk_agent(self) -> BaseAgent:
        """Retrieve the RiskAgent from the agent pool."""
        for agent in self._agents:
            if agent.category == AgentCategory.RISK:
                return agent
        raise RuntimeError("No RiskAgent found in the agent pool")

    async def _build_risk_context(self) -> dict[str, Any]:
        """Build the required context for the RiskAgent."""
        # Risk context fields like asset are usually injected by the caller,
        # but this placeholder handles future Redis lookups.
        return {}

    async def _safe_score(
        self, agent: BaseAgent, data: dict[str, Any], context: dict[str, Any]
    ) -> AgentResult:
        """Execute agent.score safely and capture latency.

        Wraps execution with time.perf_counter_ns() to record latency_ms.
        Returns a zero-score result if an exception occurs.
        """
        start_ns = time.perf_counter_ns()
        try:
            result = await agent.score(data, context)
            latency_ms = (time.perf_counter_ns() - start_ns) / 1_000_000.0
            return result.model_copy(
                update={"telemetry": AgentTelemetry(latency_ms=latency_ms)}
            )
        except Exception as exc:
            latency_ms = (time.perf_counter_ns() - start_ns) / 1_000_000.0
            logger.error("Agent execution failed | agent={} | error={}", agent.name, str(exc))
            zero = agent._make_zero_result(reason=f"Exception: {exc}")
            return zero.model_copy(
                update={"telemetry": AgentTelemetry(latency_ms=latency_ms)}
            )

    async def run(
        self,
        data: dict[str, Any],
        context: dict[str, Any] | None = None,
        asset: str = "BTC/USDT",
        timeframe: str = "30m",
        classification: QueryClassification | None = None,
    ) -> SignalOutput:
        """Execute the pipeline with 4-phase sequential logic."""
        cycle_id = str(uuid4())
        start_ns = time.perf_counter_ns()
        retrieval_depth = self._extract_depth(classification)
        trace, token = self._init_telemetry(cycle_id, asset, timeframe, context)

        try:
            await self._record_pipeline_event(
                cycle_id, asset, "cycle_started", "running",
                {"timeframe": timeframe, "retrieval_depth": retrieval_depth.value},
            )
            ctx = await self._build_enriched_context(
                context, asset, retrieval_depth, trace,
            )
            await self._record_pipeline_event(
                cycle_id, asset, "context_ready", "complete",
                self._build_rag_event_details(ctx),
            )
            risk_agent = self._get_risk_agent()
            
            res = await self._execute_all_concurrently(
                risk_agent, data, ctx, asset, timeframe, cycle_id, start_ns
            )
            trace.update(output=res.model_dump(mode='json'))
            await self._record_pipeline_event(
                cycle_id, asset, "cycle_completed", "complete",
                {
                    "decision": res.decision.value,
                    "score": res.score,
                    "raw_confluence_score": res.raw_confluence_score,
                },
            )
            await safe_monitoring_write(
                record_pipeline_cycle(
                    self._redis,
                    cycle_id=cycle_id,
                    asset=asset,
                    latency_ms=(time.perf_counter_ns() - start_ns) / 1_000_000.0,
                    status="complete",
                    score=float(res.score),
                    target_ms=self._monitoring_latency_target_ms(),
                ),
                action="record_pipeline_cycle_complete",
            )
            return res
        except Exception as e:
            trace.update(level="ERROR", status_message=str(e))
            await self._record_pipeline_event(
                cycle_id, asset, "cycle_failed", "error", {"error": str(e)},
            )
            await safe_monitoring_write(
                record_pipeline_cycle(
                    self._redis,
                    cycle_id=cycle_id,
                    asset=asset,
                    latency_ms=(time.perf_counter_ns() - start_ns) / 1_000_000.0,
                    status="error",
                    target_ms=self._monitoring_latency_target_ms(),
                ),
                action="record_pipeline_cycle_error",
            )
            await safe_monitoring_write(
                record_monitoring_alert(
                    self._redis,
                    level="ERROR",
                    message=f"Pipeline cycle failed for {asset}: {e}",
                    source="pipeline",
                    alert_id=f"cycle_failed:{asset}",
                ),
                action="record_pipeline_failure_alert",
            )
            raise
        finally:
            self._cleanup_telemetry(token)

    async def _record_pipeline_event(
        self,
        cycle_id: str,
        asset: str,
        stage: str,
        status: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Push compact pipeline telemetry for the live visualization page."""
        payload = {
            "type": "pipeline_stage",
            "cycleId": cycle_id,
            "asset": asset,
            "stage": stage,
            "status": status,
            "details": details or {},
            "cycleTs": datetime.now(timezone.utc).isoformat(),
        }
        try:
            encoded = msgspec.json.encode(payload)
            pipe = self._redis.pipeline()
            if inspect.isawaitable(pipe):
                pipe = await pipe
            lpush_result = pipe.lpush("polaris:telemetry:recent", encoded)
            if inspect.isawaitable(lpush_result):
                await lpush_result
            ltrim_result = pipe.ltrim(
                "polaris:telemetry:recent",
                0,
                _PIPELINE_EVENT_BUFFER_LENGTH - 1,
            )
            if inspect.isawaitable(ltrim_result):
                await ltrim_result
            await asyncio.wait_for(pipe.execute(), timeout=2.0)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("pipeline_event_write_failed | stage={} | error={}", stage, str(exc))

    async def _build_enriched_context(
        self, context: dict[str, Any] | None, asset: str,
        retrieval_depth: RetrievalDepth, trace: Any,
    ) -> dict[str, Any]:
        """Build context dict enriched with risk and RAG data."""
        ctx = {**(context or {}), **(await self._build_risk_context()), "asset": asset}
        
        # Tier 2 — Fear & Greed Index
        try:
            fg_provider = get_provider("fear_greed")
        except RuntimeError:
            fg_provider = None

        if fg_provider:
            fg_snapshot = await fg_provider.fetch_data()
            if fg_snapshot.data is not None:
                ctx["fear_greed_score"] = fg_snapshot.data.value
                ctx["fear_greed_classification"] = (
                    fg_snapshot.data.value_classification
                )
            ctx["fear_greed_status"] = fg_snapshot.status

        rag_contexts = await self._run_rag_retrieval(asset, retrieval_depth, trace)
        if rag_contexts:
            ctx["rag_contexts"] = rag_contexts
            ctx["rag_context_summaries"] = self._summarise_rag_contexts(rag_contexts)
        ctx["rag_status"] = self._resolve_rag_status(rag_contexts)
        if self._last_rag_error is not None:
            ctx["rag_error"] = self._last_rag_error
        await self._maybe_enrich_coingecko_context(ctx, asset)
        await self._maybe_enrich_helius_context(ctx, asset)
        return ctx

    def _polaris_base_from_pair(self, asset: str) -> str:
        """Normalize ``BTC/USDT`` or ``BTC-USDT`` to ``BTC``."""
        s = asset.strip().upper()
        if "/" in s:
            return s.split("/", 1)[0]
        if "-" in s:
            return s.split("-", 1)[0]
        return s

    async def _maybe_enrich_coingecko_context(
        self,
        ctx: dict[str, Any],
        asset: str,
    ) -> None:
        """Refresh CoinGecko /simple/price cache + load GeckoTerminal pools for DeFi bases."""
        cg = get_provider("coingecko")
        if cg is None:
            return
        base = self._polaris_base_from_pair(asset)
        gecko_id = COINGECKO_SIMPLE_PRICE_ID_BY_BASE.get(base)
        if gecko_id:
            try:
                ref = await asyncio.wait_for(
                    cg.fetch_price_reference(gecko_id),
                    timeout=8.0,
                )
                ctx["coingecko_price_reference"] = ref
                if ref.status == "healthy":
                    try:
                        cg_px = Decimal(ref.price_usd)
                        checker = ConsistencyChecker()
                        cons = await spot_price_consistency_pyth_coingecko(
                            self._redis,
                            base,
                            cg_px,
                            checker,
                        )
                        if cons is not None:
                            ctx["spot_price_cross_source"] = {
                                "is_consistent": cons.is_consistent,
                                "max_divergence_pct": cons.max_divergence_pct,
                                "divergent_providers": list(cons.divergent_providers),
                            }
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        logger.warning(
                            "spot_price_cross_source_failed | base={} | err={}",
                            base,
                            str(exc),
                        )
                    try:
                        snap_raw, hint_raw = await asyncio.gather(
                            asyncio.wait_for(
                                cg.fetch_coin_market_snapshot_tracked(gecko_id),
                                timeout=6.0,
                            ),
                            asyncio.wait_for(
                                cg.fetch_bitget_liquidity_hint(gecko_id),
                                timeout=6.0,
                            ),
                            return_exceptions=True,
                        )
                        if not isinstance(snap_raw, Exception):
                            ctx["coingecko_coin_snapshot"] = snap_raw.model_dump(mode="json")
                            ctx["coingecko_supply_shock"] = {
                                "likely_unlock_or_dilution": snap_raw.likely_unlock_or_dilution_event,
                                "circulating_pct_change_vs_prior": snap_raw.circulating_supply_pct_change_vs_prior,
                            }
                        if not isinstance(hint_raw, Exception):
                            ctx["coingecko_bitget_liquidity_hint"] = hint_raw.model_dump(
                                mode="json",
                            )
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        logger.warning(
                            "coingecko_metadata_enrich_failed | base={} | err={}",
                            base,
                            str(exc),
                        )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "coingecko_context_price_failed | base={} | err={}",
                    base,
                    str(exc),
                )
        pool_ref = GECKO_TERMINAL_TOKEN_REF_BY_BASE.get(base)
        if pool_ref:
            try:
                pools = await asyncio.wait_for(
                    cg.fetch_dex_pool_data(pool_ref),
                    timeout=8.0,
                )
                ctx["gecko_dex_pools"] = pools
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "coingecko_context_pools_failed | base={} | err={}",
                    base,
                    str(exc),
                )

    async def _maybe_enrich_helius_context(
        self,
        ctx: dict[str, Any],
        asset: str,
    ) -> None:
        """Prefetch Helius Solana exchange-flow signals for SOL/JUP scoring paths."""
        from atlas.core.asset_universe import is_helius_enabled
        from atlas.providers.helius.onchain_signals import fetch_helius_onchain_signals

        if not is_helius_enabled(asset):
            return
        try:
            onchain = await fetch_helius_onchain_signals(asset)
            if onchain is not None:
                ctx["helius_onchain"] = onchain
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "helius_context_enrich_failed | asset={} | err={}",
                asset,
                str(exc),
            )

    def _build_rag_event_details(self, ctx: dict[str, Any]) -> dict[str, Any]:
        """Build compact RAG telemetry for the live pipeline page."""
        details: dict[str, Any] = {
            "rag_status": ctx.get("rag_status", "unknown"),
            "rag_contexts": len(ctx.get("rag_contexts", [])),
        }
        if ctx.get("rag_error"):
            details["rag_error"] = ctx["rag_error"]
        if ctx.get("rag_context_summaries"):
            details["rag_summaries"] = ctx["rag_context_summaries"]
        return details

    def _resolve_rag_status(self, rag_contexts: list[ScoredDocument]) -> str:
        """Resolve human-readable RAG state for telemetry."""
        if self._rag_engine is None:
            return "disabled"
        if self._last_rag_error is not None:
            return "error"
        if len(rag_contexts) == 0:
            return "no_matches"
        return "ready"

    def _rag_hits_for_telemetry(
        self,
        rag_contexts: list[ScoredDocument],
    ) -> list[dict[str, Any]]:
        """Serialise scored documents for Memory / RAG dashboards."""
        hits: list[dict[str, Any]] = []
        for document in rag_contexts:
            payload = document.payload or {}
            preview_src = (
                payload.get("text_content")
                or payload.get("content")
                or payload.get("reasoning")
                or ""
            )
            preview = str(preview_src)[:320]
            raw_tags = payload.get("user_tags", payload.get("userTags", []))
            if isinstance(raw_tags, list):
                tags_norm = [str(t) for t in raw_tags]
            else:
                tags_norm = []
            hits.append(
                {
                    "documentId": document.document_id,
                    "similarityScore": round(float(document.similarity_score), 6),
                    "finalScore": round(
                        float(document.final_score or document.similarity_score),
                        6,
                    ),
                    "asset": str(payload.get("asset", "")),
                    "signalDecision": str(
                        payload.get(
                            "signal_decision",
                            payload.get("decision", ""),
                        ),
                    ),
                    "preview": preview,
                    "userTags": tags_norm,
                },
            )
        return hits

    def _summarise_rag_contexts(
        self,
        rag_contexts: list[ScoredDocument],
    ) -> list[dict[str, Any]]:
        """Return short document summaries safe for websocket telemetry."""
        summaries: list[dict[str, Any]] = []
        for document in rag_contexts[:3]:
            payload = document.payload
            title = payload.get("title") or payload.get("asset") or document.document_id
            text = payload.get("text_content") or payload.get("content") or ""
            summaries.append({
                "title": str(title)[:80],
                "score": round(document.final_score or document.similarity_score, 4),
                "text": str(text)[:180],
            })
        return summaries

    def _cleanup_telemetry(self, token: Any) -> None:
        """Reset trace context and flush telemetry."""
        from atlas.telemetry.langfuse_client import telemetry, current_trace_id
        current_trace_id.reset(token)
        telemetry.flush()

    def _extract_depth(
        self, classification: QueryClassification | None,
    ) -> RetrievalDepth:
        """Extract retrieval depth from classification or default.

        Args:
            classification: Optional query classification result.

        Returns:
            The retrieval depth to use for RAG search.
        """
        if classification is not None:
            return classification.retrieval_depth
        return RetrievalDepth.DEFAULT

    async def _run_rag_retrieval(
        self,
        asset: str,
        retrieval_depth: RetrievalDepth,
        trace: Any,
    ) -> list[ScoredDocument]:
        """Execute RAG context retrieval if engine is available.

        Args:
            asset: The asset being analysed.
            retrieval_depth: Depth from query classification.
            trace: Langfuse trace for metadata attachment.

        Returns:
            List of scored documents, empty if no engine or error.
        """
        if self._rag_engine is None:
            return []

        top_k, ef = self._rag_engine._depth_to_params(retrieval_depth)
        query_text = f"market context for {asset}"
        try:
            results = await self._rag_engine.find_similar_contexts(
                query=query_text,
                retrieval_depth=retrieval_depth,
                qdrant_filter=Filter(
                    must=[
                        FieldCondition(
                            key="asset",
                            match=MatchValue(value=asset),
                        ),
                    ],
                ),
            )
            self._log_rag_retrieval(retrieval_depth, top_k, ef, len(results))
            trace.update(metadata=self._rag_trace_metadata(
                retrieval_depth, top_k, ef, len(results),
            ))
            self._last_rag_error = None
            telemetry_event = build_retrieval_event_dict(
                asset=asset,
                query_text=query_text,
                retrieval_depth=retrieval_depth.value,
                hit_count=len(results),
                hits=self._rag_hits_for_telemetry(results),
            )
            await append_retrieval_event(self._redis, telemetry_event)
            return results
        except Exception as exc:
            self._last_rag_error = str(exc)
            logger.error("RAG retrieval failed | error={}", self._last_rag_error)
            err_event = build_retrieval_event_dict(
                asset=asset,
                query_text=query_text,
                retrieval_depth=retrieval_depth.value,
                hit_count=0,
                hits=[],
                error=self._last_rag_error,
            )
            await append_retrieval_event(self._redis, err_event)
            return []

    def _log_rag_retrieval(
        self,
        depth: RetrievalDepth,
        top_k: int,
        ef: int,
        found: int,
    ) -> None:
        """Log RAG retrieval parameters and result count.

        Args:
            depth: The retrieval depth used.
            top_k: Number of results requested.
            ef: HNSW ef parameter used.
            found: Number of results returned.
        """
        logger.info(
            "rag_query | depth={} | top_k={} | ef={} | freshness_lambda={} | found={}",
            depth.value, top_k, ef,
            self._settings.rag_freshness_lambda_per_hour, found,
        )

    def _rag_trace_metadata(
        self,
        depth: RetrievalDepth,
        top_k: int,
        ef: int,
        found: int,
    ) -> dict[str, Any]:
        """Build Langfuse trace metadata for RAG retrieval.

        Args:
            depth: The retrieval depth used.
            top_k: Number of results requested.
            ef: HNSW ef parameter used.
            found: Number of results returned.

        Returns:
            Dict of metadata fields for Langfuse trace.
        """
        return {
            "rag_depth": depth.value,
            "rag_top_k": top_k,
            "rag_hnsw_ef": ef,
            "rag_freshness_lambda": self._settings.rag_freshness_lambda_per_hour,
            "rag_results_found": found,
        }

    def _init_telemetry(
        self, cycle_id: str, asset: str, timeframe: str, context: dict[str, Any] | None,
    ) -> tuple[Any, Any]:
        """Set up Langfuse trace and context token for the cycle."""
        from atlas.telemetry.langfuse_client import telemetry, current_trace_id
        token = current_trace_id.set(cycle_id)
        trace = telemetry.trace(
            name="orchestrator_cycle", id=cycle_id,
            input={"asset": asset, "timeframe": timeframe, "context": context},
        )
        return trace, token

    def _get_agent_timeout(self, agent_name: str) -> float:
        """Get specific timeouts for agents."""
        name = agent_name.lower()
        if "risk" in name:
            # Risk runs several parallel Redis reads; 80 ms routinely timed out in dev,
            # which marked the agent DEGRADED and spammed monitoring alerts.
            return 0.35
        if "microstructure" in name or "sentiment" in name or "derivatives" in name:
            return 4.0
        if "macro" in name:
            return 5.0
        if "onchain" in name:
            return 8.0
        return 4.0

    @staticmethod
    def _bias_to_signal_direction(bias: str) -> SignalDirection:
        lowered = bias.lower()
        if lowered == "long":
            return SignalDirection.BULLISH
        if lowered == "short":
            return SignalDirection.BEARISH
        return SignalDirection.NEUTRAL

    async def _execute_simple_confluence_path(
        self,
        risk_agent: BaseAgent,
        data: dict[str, Any],
        ctx: dict[str, Any],
        asset: str,
        timeframe: str,
        cycle_id: str,
        start_ns: int,
    ) -> SignalOutput:
        """Risk gate plus deterministic ``ConfluenceScoringEngine`` (paper-validation mode)."""
        risk_result = await self._safe_score_with_timeout(
            risk_agent,
            data,
            ctx,
            self._get_agent_timeout(risk_agent.name),
        )
        if risk_result.veto:
            veto_signal = await self._handle_veto(
                risk_result, asset, timeframe, cycle_id, start_ns,
            )
            return self._enrich_veto_signal(veto_signal)

        all_signals = await build_all_signals_from_market_payload_async(data, ctx, asset)
        premium_contrib, premium_signal = await fetch_premium_scoring_inputs(
            self._redis,
            asset,
        )
        if premium_contrib != 0.0 or premium_signal != "n/a":
            all_signals = msgspec.structs.replace(
                all_signals,
                derivatives=msgspec.structs.replace(
                    all_signals.derivatives,
                    coinbase_premium_contribution=premium_contrib,
                    coinbase_premium_signal=premium_signal,
                ),
            )
        regime_label = str(ctx.get("macro_regime") or ctx.get("regime_label") or "normal")
        regime_ctx = RegimeContext(
            regime_label=regime_label,
            multiplier_category="normal_conditions",
        )
        engine = ConfluenceScoringEngine()
        conv = await engine.calculate(all_signals, regime_ctx)
        direction = self._bias_to_signal_direction(conv.bias)

        analyst_results: list[AgentResult] = []
        for agent in self._agents:
            if agent.category == AgentCategory.RISK:
                continue
            pillar = _SIMPLE_MODE_BREAKDOWN_FOR_AGENT.get(agent.name)
            if pillar is None:
                continue
            cat = conv.breakdown[pillar]
            stub = AgentResult(
                agent_name=agent.name,
                score=int(min(cat.score, 220)),
                max_score=int(cat.max),
                weight=1.0,
                direction=direction,
                explanation="simple_pipeline_mode:{}".format(pillar),
                convergences=list(cat.factors),
                risks=[],
                veto=False,
            )
            analyst_results.append(stub)
            await self._persist_agent_status(agent, stub)

        return await self._finalize_concurrent_execution(
            analyst_results + [risk_result],
            risk_result,
            risk_agent,
            asset,
            timeframe,
            cycle_id,
            start_ns,
            ctx,
        )

    async def _execute_all_concurrently(
        self, risk_agent: BaseAgent, data: dict[str, Any], ctx: dict[str, Any],
        asset: str, timeframe: str, cycle_id: str, start_ns: int,
    ) -> SignalOutput:
        """Execute all agents concurrently with FIRST_COMPLETED for fast-path veto."""
        if self._settings.simple_pipeline_mode:
            return await self._execute_simple_confluence_path(
                risk_agent, data, ctx, asset, timeframe, cycle_id, start_ns,
            )
        pending_tasks, task_to_agent = self._create_agent_tasks(data, ctx)
        results: list[AgentResult] = []
        risk_result: AgentResult | None = None
        
        while pending_tasks:
            done, pending_tasks = await asyncio.wait(
                pending_tasks, return_when=asyncio.FIRST_COMPLETED
            )
            
            for task in done:
                res = cast(AgentResult, task.result())
                results.append(res)
                agent = task_to_agent[task]
                
                if agent.name == risk_agent.name:
                    risk_result = res
                    if risk_result.veto:
                        cancel_tasks = list(pending_tasks)
                        for p in cancel_tasks:
                            p.cancel()
                        pending_tasks.clear()

                        for task in cancel_tasks:
                            ag = task_to_agent.get(task)
                            if ag is None:
                                continue
                            await self._persist_cancelled_agent_placeholder(ag)

                        signal = await self._handle_veto(risk_result, asset, timeframe, cycle_id, start_ns)
                        return self._enrich_veto_signal(signal)

        return await self._finalize_concurrent_execution(
            results, risk_result, risk_agent, asset, timeframe, cycle_id, start_ns, ctx
        )

    def _create_agent_tasks(self, data: dict[str, Any], ctx: dict[str, Any]) -> tuple[set[asyncio.Task[AgentResult]], dict[asyncio.Task[AgentResult], BaseAgent]]:
        """Create initial tasks for all agents."""
        pending_tasks = set()
        task_to_agent = {}
        for agent in self._agents:
            timeout = self._get_agent_timeout(agent.name)
            task = asyncio.create_task(
                self._safe_score_with_timeout(agent, data, ctx, timeout),
                name=agent.name
            )
            pending_tasks.add(task)
            task_to_agent[task] = agent
        return pending_tasks, task_to_agent

    async def _finalize_concurrent_execution(
        self, results: list[AgentResult], risk_result: AgentResult | None,
        risk_agent: BaseAgent, asset: str, timeframe: str, cycle_id: str, start_ns: int, ctx: dict[str, Any]
    ) -> SignalOutput:
        """Process results from concurrent execution when no early veto occurred."""


        # Separate risk and analyst results
        if risk_result is None:
            # Fallback if risk agent task somehow failed to return an AgentResult
            risk_result = risk_agent._make_zero_result(reason="Risk agent missing")
            results.append(risk_result)
            
        assert risk_result is not None
            
        analyst_results = [r for r in results if r.agent_name != risk_agent.name]
        self._log_telemetry([risk_result] + analyst_results)

        if not self._check_quorum(analyst_results):
            return await self._handle_quorum_failure(
                risk_agent, asset, timeframe, cycle_id, start_ns,
            )

        lat = (time.perf_counter_ns() - start_ns) / 1_000_000.0
        signal = await self._synthesiser.synthesize(
            analyst_results=[risk_result] + analyst_results,
            position_size=Decimal("0"),
            asset=asset,
            timeframe=timeframe,
            cycle_id=cycle_id,
            cycle_latency_ms=lat,
        )
        blocked = self._apply_min_trade_score_gate(signal, asset)
        if blocked is not None:
            return blocked

        await self._run_portfolio_optimisation(asset, analyst_results)
        all_results = [risk_result] + analyst_results
        gated = self._apply_confidence_gate(signal, all_results)
        reflected = await self._apply_reflection(gated, ctx)
        return await self._attach_deepseek_evaluation(reflected, all_results, ctx)

    def _apply_min_trade_score_gate(
        self,
        signal: SignalOutput,
        asset: str,
    ) -> SignalOutput | None:
        """Fail-fast execution gate: below-threshold scores skip heavy post-steps.

        Returns a NO_POSITION signal when gated; ``None`` when the setup may proceed.
        """
        floor = self._settings.min_trade_score
        if signal.score >= floor:
            return None
        log_skipped_opportunity(
            asset=asset,
            timestamp=signal.timestamp,
            score=int(signal.score),
            reason="normalised_score_below_min_trade_score",
            min_trade_score=int(floor),
            raw_confluence_score=int(signal.raw_confluence_score),
        )
        return signal.model_copy(
            update={
                "decision": SignalDecision.NO_POSITION,
                "action": None,
                "key_risks": [*list(signal.key_risks), "BELOW_MIN_TRADE_SCORE"],
            },
        )

    async def _attach_deepseek_evaluation(
        self,
        signal: SignalOutput,
        agent_results: list[AgentResult],
        ctx: dict[str, Any],
    ) -> SignalOutput:
        """Attach structured DeepSeek reasoning from the RAG intelligence matrix."""
        if self._settings.simple_pipeline_mode:
            return signal

        if self._deepseek_client is None:
            return signal

        if not self._settings.deepseek_api_key.get_secret_value():
            fallback = build_safe_fallback_decision("DEEPSEEK_API_KEY_MISSING")
            return signal.model_copy(update={"deepseek_evaluation": fallback})

        context_matrix = self._build_deepseek_context_matrix(
            signal,
            agent_results,
            ctx,
        )
        try:
            decision = await self._deepseek_client.evaluate_signal(context_matrix)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("deepseek_pipeline_eval_failed | asset={} | error={}", signal.asset, str(exc))
            decision = build_safe_fallback_decision(str(exc))

        return signal.model_copy(update={"deepseek_evaluation": decision})

    def parse_synthesis_layer(
        self,
        raw_llm_response: str,
        symbol: str,
        *,
        latency_ms: int = 0,
        synthesis_model: str | None = None,
    ) -> SynthesisOutput:
        """Validate LLM synthesis JSON; never raises on malformed output."""
        model_label = synthesis_model or self._settings.deepseek_model
        return parse_synthesis_or_fallback(
            raw_llm_response,
            symbol=symbol,
            synthesis_model=model_label,
            latency_ms=latency_ms,
        )

    def _build_deepseek_context_matrix(
        self,
        signal: SignalOutput,
        agent_results: list[AgentResult],
        ctx: dict[str, Any],
    ) -> str:
        """Build compact JSON-style matrix for DeepSeek RAG reasoning."""
        payload = {
            "v": "2.1",
            "id": signal.signal_id,
            "ts": signal.timestamp.isoformat(),
            "sym": signal.asset,
            "tf": signal.timeframe,
            "px": _latest_scalar(ctx.get("close")),
            "scores": signal.category_scores.model_dump(mode="json"),
            "agents": {
                _agent_matrix_key(result.agent_name): _agent_matrix_payload(result)
                for result in agent_results
            },
            "rag": {
                "status": ctx.get("rag_status", "unknown"),
                "contexts": len(ctx.get("rag_contexts", [])),
                "error": ctx.get("rag_error"),
                "summaries": ctx.get("rag_context_summaries", []),
            },
            "flags": _collect_matrix_flags(agent_results),
            "risk_state": signal.confidence_tier.value,
            "degraded_providers": [],
            "instruction": (
                "Return only a JSON object matching DeepSeekDecision fields: "
                "decision, confidence, cross_correlation_grade, key_convergences, "
                "key_risks, reasoning, would_change_if. Decision must use one of "
                "Strong Buy, Buy, Hold, Sell, Strong Sell, No Position."
            ),
        }
        encoded = msgspec.json.encode(payload)
        return encoded.decode("utf-8")

    def _enrich_veto_signal(self, res: SignalOutput) -> SignalOutput:
        """Attach NO_POSITION decision and fallback DeepSeek evaluation."""
        if res.deepseek_evaluation is None:
            from atlas.models.signal import build_safe_fallback_decision
            ds_dec = build_safe_fallback_decision("RISK_AGENT_TIMEOUT_VETO")
        else:
            ds_dec = res.deepseek_evaluation
        ds_dec = ds_dec.model_copy(
            update={"decision": SignalDecision.NO_POSITION, "key_risks": ["RISK_AGENT_TIMEOUT_VETO"]},
        )
        return res.model_copy(
            update={"decision": SignalDecision.NO_POSITION, "deepseek_evaluation": ds_dec},
        )

    async def _apply_reflection(
        self,
        signal: SignalOutput,
        context: dict[str, Any],
    ) -> SignalOutput:
        """Apply optional reflection as metadata/sizing overlay only."""
        if self._settings.simple_pipeline_mode:
            return signal
        if self._reflection_critic is None:
            return signal

        envelope = await self._reflection_critic.reflect(signal, context)
        logger.info(
            "reflection_result | selected_round={} | selected_score={} | human_review={}",
            envelope.result.selected_round,
            envelope.result.selected_score,
            envelope.result.should_human_review,
        )
        return envelope.signal

    def _apply_confidence_gate(
        self,
        signal: SignalOutput,
        agent_results: list[AgentResult],
    ) -> SignalOutput:
        """Apply derived confidence gate to the synthesized signal."""
        calculator = PipelineConfidenceCalculator(self._settings)
        inputs = self._collect_confidence_inputs(signal, agent_results)
        confidence, dimensions = calculator.calculate(inputs)
        tier, modifier, human_review = calculator.classify_tier(
            confidence, signal.score,
        )
        update: dict[str, object] = {
            "pipeline_confidence": confidence,
            "confidence_dimensions": dimensions,
            "confidence_tier": tier,
            "position_size_modifier": modifier,
            "human_review_flag": human_review,
        }
        if tier == ConfidenceTier.SKIP:
            update["decision"] = SignalDecision.NO_POSITION
            update["action"] = None
        gated = signal.model_copy(update=update)
        self._log_confidence_gate(gated, confidence, tier, modifier, human_review)
        return gated

    def _collect_confidence_inputs(
        self,
        signal: SignalOutput,
        agent_results: list[AgentResult],
    ) -> PipelineConfidenceInputs:
        """Gather all dimension inputs from existing pipeline state."""
        return PipelineConfidenceInputs(
            conviction_point_estimate=signal.score,
            conviction_lower=None,
            agents_dispatched=len(agent_results),
            agents_with_live_data=sum(
                1 for r in agent_results if r.score > 0
            ),
            anomaly_flag_count=0,
            consistency_warning_count=0,
            regime_transition_probability=None,
            max_price_divergence_pct=None,
        )

    def _log_confidence_gate(
        self,
        signal: SignalOutput,
        confidence: float,
        tier: ConfidenceTier,
        modifier: float,
        human_review: bool,
    ) -> None:
        """Log confidence gate outcome for all tiers."""
        if tier == ConfidenceTier.SKIP:
            logger.warning(
                "CONFIDENCE_GATE_SKIP | confidence={} | score={} | tier={}",
                confidence, signal.score, tier.value,
            )
        else:
            logger.info(
                "confidence_gate | tier={} | confidence={} | score={}"
                " | modifier={} | human_review={}",
                tier.value, confidence, signal.score, modifier, human_review,
            )

    async def _run_portfolio_optimisation(self, asset: str, results: list[AgentResult]) -> Decimal:
        """Phase 2: Calculate position size using PortfolioOptimisationAgent."""
        from atlas.agents.risk.portfolio_optimisation_agent import PortfolioOptimisationAgent

        try:
            agent = PortfolioOptimisationAgent(self._redis)
            size = await asyncio.wait_for(
                agent.calculate_position_size(asset, results), timeout=0.05
            )
            return size
        except Exception as e:
            logger.warning("portfolio_optimisation_failed | error={}", str(e))
        return Decimal("0.0")

    async def _safe_score_with_timeout(
        self, agent: BaseAgent, data: dict[str, Any], context: dict[str, Any], timeout: float
    ) -> AgentResult:
        """Execute agent score with strict timeout and warmup gating.

        State handling:
            WARMING_UP → return warmup result immediately (no scoring).
            FAILED     → return zero result with failure reason.
            DEGRADED   → still score (functioning with reduced quality).
            READY      → normal scoring path.
        """
        if agent.state == AgentState.WARMING_UP:
            logger.info(
                "agent_warmup_skip | agent={} | state={} | samples={}/{}",
                agent.name, agent.state.value,
                agent.sample_count, agent.MIN_SAMPLES_TO_EMIT,
            )
            warmup_res = agent._make_warmup_result()
            await self._persist_agent_status(agent, warmup_res)
            return warmup_res
        if agent.state == AgentState.FAILED:
            logger.error(
                "agent_failed_skip | agent={} | state=FAILED",
                agent.name,
            )
            failed_res = agent._make_zero_result(
                reason="Agent in FAILED state — requires restart",
            )
            await self._persist_agent_status(agent, failed_res)
            return failed_res
        try:
            scored = await asyncio.wait_for(self._safe_score(agent, data, context), timeout=timeout)
            await self._persist_agent_status(agent, scored)
            return scored
        except asyncio.TimeoutError:
            logger.warning("Agent timed out | agent={} | budget_ms={}", agent.name, int(timeout * 1000))
            zero = agent._make_zero_result(reason=f"Timed out ({int(timeout * 1000)}ms budget)")
            timed_out = zero.model_copy(update={"telemetry": AgentTelemetry(latency_ms=timeout * 1000.0)})
            await self._persist_agent_status(agent, timed_out)
            return timed_out



    async def _handle_veto(
        self,
        risk_result: AgentResult,
        asset: str,
        timeframe: str,
        cycle_id: str,
        cycle_start_ns: int,
    ) -> SignalOutput:
        """Return veto signal."""
        logger.warning("Fast-path veto triggered | reasons={}", risk_result.risks)
        await append_risk_veto_event(
            self._redis,
            asset=asset,
            reasons=list(risk_result.risks),
            cycle_id=cycle_id,
        )
        latency_ms = (time.perf_counter_ns() - cycle_start_ns) / 1_000_000.0
        return await self._synthesiser.synthesize(
            [risk_result], Decimal("0"), asset, timeframe, cycle_id=cycle_id, cycle_latency_ms=latency_ms
        )

    async def _handle_quorum_failure(
        self, risk_agent: BaseAgent, asset: str, timeframe: str, cycle_id: str, cycle_start_ns: int
    ) -> SignalOutput:
        """Return a No Position signal when quorum isn't met."""
        logger.critical("Quorum failure: insufficient valid categories")
        veto_res = risk_agent._make_zero_result(reason="Quorum failure").model_copy(update={"veto": True})
        latency_ms = (time.perf_counter_ns() - cycle_start_ns) / 1_000_000.0
        return await self._synthesiser.synthesize(
            [veto_res], Decimal("0"), asset, timeframe, cycle_id=cycle_id, cycle_latency_ms=latency_ms
        )

    def _check_quorum(self, results: list[AgentResult]) -> bool:
        """Ensure minimum categories are present, READY, and not zero-scored.

        Only agents whose state is READY at execution time are counted.
        Agents still WARMING_UP have their results tagged with
        'AGENT_WARMING_UP' in risks — these are excluded from quorum.
        """
        active_categories: set[AgentCategory] = set()

        for r in results:
            # Skip warmup / timeout / exception results
            is_warmup = "AGENT_WARMING_UP" in r.risks
            is_error = (
                r.explanation.startswith("Timed out")
                or r.explanation.startswith("Exception")
            )
            if is_warmup or is_error:
                continue

            for agent in self._agents:
                if agent.name == r.agent_name:
                    active_categories.add(agent.category)
                    break

        return len(active_categories) >= self.QUORUM_MIN_CATEGORIES

    def _log_telemetry(self, results: list[AgentResult]) -> None:
        """Log latency telemetry for all agents after a cycle."""
        for r in results:
            timed_out = r.explanation.startswith("Timed out")
            logger.info(
                "Agent telemetry | agent={} | latency_ms={} | timed_out={}",
                r.agent_name,
                round(r.telemetry.latency_ms, 2),
                timed_out
            )


def _latest_scalar(value: Any) -> str:
    """Return the latest scalar from a provider value for LLM context."""
    if isinstance(value, list) and value:
        return str(value[-1])
    if value is None:
        return ""
    return str(value)


def _agent_matrix_key(agent_name: str) -> str:
    """Compact agent labels used by DeepSeek intelligence matrices."""
    aliases = {
        "derivatives": "DA",
        "technical": "TA",
        "onchain": "OA",
        "sentiment": "SNA",
        "regime": "MRA",
        "risk": "RA",
    }
    return aliases.get(agent_name, agent_name.upper())


def _agent_matrix_payload(result: AgentResult) -> dict[str, Any]:
    """Compact one-agent payload for DeepSeek."""
    payload: dict[str, Any] = {
        "s": result.score,
        "max": result.max_score,
        "dir": result.direction.value,
        "f": [*result.convergences, *result.risks],
        "explain": result.explanation,
    }
    if result.veto:
        payload["veto"] = True
    if result.sub_signals:
        payload["d"] = _normalise_matrix_sub_signals(result.sub_signals)
    return payload


def _normalise_matrix_sub_signals(sub_signals: dict[str, Any]) -> dict[str, Any]:
    """Convert sub-signals into a JSON-safe compact map."""
    normalised: dict[str, Any] = {}
    for key, value in sub_signals.items():
        if hasattr(value, "model_dump"):
            dumped = value.model_dump(mode="json")
            normalised[key] = {
                "value": dumped.get("value"),
                "flag": dumped.get("flag"),
            }
        elif isinstance(value, (str, int, float, bool)) or value is None:
            normalised[key] = value
        else:
            normalised[key] = str(value)
    return normalised


def _collect_matrix_flags(agent_results: list[AgentResult]) -> list[str]:
    """Flatten convergence/risk flags for the LLM matrix."""
    flags: list[str] = []
    seen: set[str] = set()
    for result in agent_results:
        for flag in [*result.convergences, *result.risks]:
            if flag in seen:
                continue
            seen.add(flag)
            flags.append(flag)
    return flags
