"""Output Processor — final ATLAS stage before PROMETHEUS.

S3-P9 canonical implementation. Takes the authoritative SignalOutput from
ConfluenceScorer and the raw LLM text from LLMRouter. Validates both,
enriches the signal with LLM reasoning, emits to Redis pub/sub, and
writes to RAG memory + frontend activity stream.

CRITICAL RULE:
    The LLM provides REASONING TEXT only. The score comes from
    ConfluenceScorer. The Output Processor must NEVER use any score
    or number from the LLM raw text as the actual signal score.
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Literal

import msgspec
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from atlas.models.signal import AgentResult, SignalOutput
from atlas.pipeline.execution_gate import log_skipped_opportunity

if TYPE_CHECKING:
    from redis.asyncio import Redis

    from atlas.pipeline.llm_router import LLMResponse
    from atlas.pipeline.pubsub import RedisSignalPublisher
    from atlas.rag.pipeline import RAGPipeline
    from atlas.shared.config import PolarisSettings


# ---------------------------------------------------------------------------
# Decision Literals
# ---------------------------------------------------------------------------

DecisionLabel = Literal[
    "STRONG_BUY",
    "BUY",
    "HOLD",
    "SELL",
    "STRONG_SELL",
    "NO_POSITION",
]


# ---------------------------------------------------------------------------
# ProcessedSignal — frozen Pydantic v2 model
# ---------------------------------------------------------------------------


class ProcessedSignal(BaseModel):
    """Final enriched signal emitted to PROMETHEUS and activity stream.

    The ``signal`` field carries the authoritative ConfluenceScorer output.
    LLM-derived reasoning is captured in ``reasoning_summary``,
    ``key_convergences``, ``key_risks``, and ``suggested_next_actions``.
    """

    model_config = ConfigDict(frozen=True)

    signal_id: str
    asset: str
    signal: SignalOutput
    decision: DecisionLabel
    reasoning_summary: str
    key_convergences: list[str] = Field(default_factory=list)
    key_risks: list[str] = Field(default_factory=list)
    suggested_next_actions: list[str] = Field(default_factory=list)
    model_used: str
    escalation_reason: str | None = None
    cycle_timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    emit_to_prometheus: bool = False


# ---------------------------------------------------------------------------
# OutputProcessor
# ---------------------------------------------------------------------------


class OutputProcessor:
    """Validate, enrich, and emit the final ATLAS signal.

    Args:
        settings: PolarisSettings instance.
        rag_pipeline: RAGPipeline for writing signal memory.
        signal_publisher: RedisSignalPublisher for pub/sub emission.
        redis_client: ``redis.asyncio.Redis`` for activity stream writes.
    """

    def __init__(
        self,
        settings: PolarisSettings,
        rag_pipeline: RAGPipeline,
        signal_publisher: RedisSignalPublisher,
        redis_client: Redis,  # type: ignore[type-arg]
    ) -> None:
        """Initialize OutputProcessor with injected dependencies."""
        self._settings = settings
        self._rag = rag_pipeline
        self._publisher = signal_publisher
        self._redis = redis_client

    # ── Public API ────────────────────────────────────────────────────

    async def process(
        self,
        signal: SignalOutput,
        llm_response: LLMResponse,
        agent_results: list[AgentResult],
        asset: str,
    ) -> ProcessedSignal:
        """Validate, enrich, and emit a ProcessedSignal.

        Returns a ProcessedSignal regardless of success/failure —
        the pipeline never raises from this method.
        """
        if signal.score < self._settings.min_trade_score:
            if "BELOW_MIN_TRADE_SCORE" not in signal.key_risks:
                log_skipped_opportunity(
                    asset=asset,
                    timestamp=signal.timestamp,
                    score=int(signal.score),
                    reason="output_processor_suppressed_emit",
                    min_trade_score=int(self._settings.min_trade_score),
                    raw_confluence_score=int(signal.raw_confluence_score),
                )
            return ProcessedSignal(
                signal_id=_generate_signal_id(asset),
                asset=asset,
                signal=signal,
                decision="NO_POSITION",
                reasoning_summary="Score below minimum trade threshold — no emission.",
                model_used=llm_response.model_used,
                escalation_reason=llm_response.escalation_reason,
                cycle_timestamp=llm_response.cycle_timestamp,
                emit_to_prometheus=False,
            )

        # LLM error fast path
        if llm_response.raw_text == "PIPELINE_ERROR":
            return await self._handle_pipeline_error(
                signal, llm_response, asset,
            )

        # Validate signal integrity
        if not self._validate_signal(signal):
            return await self._handle_validation_failure(
                signal, llm_response, asset,
            )

        # Build ProcessedSignal
        processed = self._build_processed_signal(
            signal, llm_response, asset,
        )

        # Emit and persist (non-blocking failures)
        await self._emit_and_persist(
            processed, signal, agent_results, asset,
        )
        return processed

    # ── Signal Building ───────────────────────────────────────────────

    def _build_processed_signal(
        self,
        signal: SignalOutput,
        llm_response: LLMResponse,
        asset: str,
    ) -> ProcessedSignal:
        """Construct a ProcessedSignal from validated inputs."""
        decision = self._determine_decision(signal.score)
        reasoning, convergences, risks, actions = self._extract_reasoning(
            llm_response.raw_text,
        )
        signal_id = _generate_signal_id(asset)
        should_emit = decision in ("STRONG_BUY", "BUY")

        return ProcessedSignal(
            signal_id=signal_id,
            asset=asset,
            signal=signal,
            decision=decision,
            reasoning_summary=reasoning,
            key_convergences=convergences,
            key_risks=risks,
            suggested_next_actions=actions,
            model_used=llm_response.model_used,
            escalation_reason=llm_response.escalation_reason,
            cycle_timestamp=llm_response.cycle_timestamp,
            emit_to_prometheus=should_emit,
        )

    # ── Decision Mapping ──────────────────────────────────────────────

    def _determine_decision(self, score: int) -> DecisionLabel:
        """Map normalised 0-100 score to a decision label.

        Entry-side only — exits are handled by ExitScorer (S2-P4).
        """
        if score >= 82:
            return "STRONG_BUY"
        if score >= 68:
            return "BUY"
        if score >= 55:
            return "HOLD"
        return "NO_POSITION"

    # ── Reasoning Extraction ──────────────────────────────────────────

    def _extract_reasoning(
        self,
        llm_raw_text: str,
    ) -> tuple[str, list[str], list[str], list[str]]:
        """Parse LLM output for reasoning, convergences, risks, and actions.

        Returns:
            A 4-tuple of (reasoning_summary, convergences, risks, actions).
        """
        sections = _parse_llm_sections(llm_raw_text)

        if sections is not None:
            return _build_structured_extraction(sections)

        return _build_fallback_extraction(llm_raw_text)

    # ── Signal Validation ─────────────────────────────────────────────

    def _validate_signal(self, signal: SignalOutput) -> bool:
        """Return True iff the signal passes all integrity checks."""
        checks = [
            _check_score_range(signal.score),
            _check_confidence_range(signal.confidence),
            _check_raw_score_cap(signal.category_scores.total),
            _check_telemetry_present(signal.telemetry),
            _check_schema_v2_fields(signal),
        ]
        if all(checks):
            return True

        logger.error(
            "signal_validation_failed | asset={} | score={} | confidence={}",
            signal.asset,
            signal.score,
            str(signal.confidence),
        )
        return False

    # ── Emission & Persistence ────────────────────────────────────────

    async def _emit_and_persist(
        self,
        processed: ProcessedSignal,
        signal: SignalOutput,
        agent_results: list[AgentResult],
        asset: str,
    ) -> None:
        """Emit to PROMETHEUS, write RAG memory, and activity stream."""
        if processed.emit_to_prometheus:
            await self._emit_to_prometheus(processed, agent_results)

        await self._write_to_activity_stream(processed)

    async def _emit_to_prometheus(
        self,
        processed: ProcessedSignal,
        agent_results: list[AgentResult],
    ) -> None:
        """Publish ProcessedSignal to ``polaris:signals:{asset}``."""
        channel = f"polaris:signals:{processed.asset}"
        try:
            payload = msgspec.json.encode(
                processed.model_dump(mode="json"),
            )
            await asyncio.wait_for(
                self._redis.publish(channel, payload),
                timeout=5.0,
            )
        except Exception as exc:
            logger.critical(
                "prometheus_publish_failed | asset={} | error={}",
                processed.asset,
                str(exc),
            )
            return

        # Write to RAG memory after successful publish
        await self._write_rag_memory(processed, agent_results)

        logger.info(
            "signal_emitted_to_prometheus | asset={} | decision={} | score={} | model_used={}",
            processed.asset,
            processed.decision,
            processed.signal.score,
            processed.model_used,
        )

    async def _write_rag_memory(
        self,
        processed: ProcessedSignal,
        agent_results: list[AgentResult],
    ) -> None:
        """Persist signal memory to RAG pipeline."""
        try:
            await self._rag.write_signal_memory(
                processed.signal,
                processed.asset,
                agent_results,
                processed.cycle_timestamp,
            )
        except Exception as exc:
            logger.error(
                "rag_write_signal_memory_failed | asset={} | error={}",
                processed.asset,
                str(exc),
            )

    async def _write_to_activity_stream(
        self,
        processed: ProcessedSignal,
    ) -> None:
        """LPUSH activity record to Redis list ``activity:stream``."""
        activity_record = _build_activity_record(processed)
        try:
            encoded = msgspec.json.encode(activity_record)
            pipe = self._redis.pipeline()
            pipe.lpush("activity:stream", encoded)
            pipe.ltrim("activity:stream", 0, 999)
            await asyncio.wait_for(pipe.execute(), timeout=5.0)
        except Exception as exc:
            logger.critical(
                "activity_stream_write_failed | asset={} | error={}",
                processed.asset,
                str(exc),
            )

    # ── Error Paths ───────────────────────────────────────────────────

    async def _handle_pipeline_error(
        self,
        signal: SignalOutput,
        llm_response: LLMResponse,
        asset: str,
    ) -> ProcessedSignal:
        """Build a safe NO_POSITION ProcessedSignal on LLM failure."""
        processed = ProcessedSignal(
            signal_id=_generate_signal_id(asset),
            asset=asset,
            signal=signal,
            decision="NO_POSITION",
            reasoning_summary="Pipeline error — LLM unavailable this cycle.",
            model_used=llm_response.model_used,
            escalation_reason=llm_response.escalation_reason,
            cycle_timestamp=llm_response.cycle_timestamp,
            emit_to_prometheus=False,
        )
        await self._write_to_activity_stream(processed)
        return processed

    async def _handle_validation_failure(
        self,
        signal: SignalOutput,
        llm_response: LLMResponse,
        asset: str,
    ) -> ProcessedSignal:
        """Build a safe NO_POSITION ProcessedSignal on validation failure."""
        processed = ProcessedSignal(
            signal_id=_generate_signal_id(asset),
            asset=asset,
            signal=signal,
            decision="NO_POSITION",
            reasoning_summary="Signal validation failed — suppressed.",
            model_used=llm_response.model_used,
            escalation_reason=llm_response.escalation_reason,
            cycle_timestamp=llm_response.cycle_timestamp,
            emit_to_prometheus=False,
        )
        await self._write_to_activity_stream(processed)
        return processed


# ---------------------------------------------------------------------------
# Pure helpers (no I/O, no state)
# ---------------------------------------------------------------------------

_SECTION_HEADERS = (
    "REASONING:",
    "CONVERGENCES:",
    "RISKS:",
    "NEXT ACTIONS:",
)


def _generate_signal_id(asset: str) -> str:
    """Generate ``UTC_timestamp + asset + 8-char SHA`` signal ID."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    hash_part = hashlib.sha256(
        f"{ts}{asset}".encode(),
    ).hexdigest()[:8]
    return f"{ts}_{asset}_{hash_part}"


def _parse_llm_sections(
    raw_text: str,
) -> dict[str, str] | None:
    """Attempt to parse structured LLM output by section headers.

    Returns None if no recognized headers are found.
    """
    upper = raw_text.upper()
    found_any = any(h in upper for h in _SECTION_HEADERS)
    if not found_any:
        return None

    sections: dict[str, str] = {}
    for header in _SECTION_HEADERS:
        idx = upper.find(header)
        if idx == -1:
            sections[header] = ""
            continue
        start = idx + len(header)
        end = _find_next_header(upper, start)
        sections[header] = raw_text[start:end].strip()
    return sections


def _find_next_header(upper_text: str, start: int) -> int:
    """Find the position of the next section header after ``start``."""
    positions = []
    for h in _SECTION_HEADERS:
        pos = upper_text.find(h, start)
        if pos != -1:
            positions.append(pos)
    return min(positions) if positions else len(upper_text)


def _build_structured_extraction(
    sections: dict[str, str],
) -> tuple[str, list[str], list[str], list[str]]:
    """Build extraction from parsed structured sections."""
    reasoning = sections.get("REASONING:", "").strip()
    convergences = _split_list_section(sections.get("CONVERGENCES:", ""))
    risks = _split_list_section(sections.get("RISKS:", ""))
    actions = _split_list_section(sections.get("NEXT ACTIONS:", ""))

    return (
        reasoning[:1200],
        convergences[:5],
        risks[:5],
        actions[:3],
    )


def _build_fallback_extraction(
    raw_text: str,
) -> tuple[str, list[str], list[str], list[str]]:
    """Fallback: first 2 sentences as reasoning, empty lists."""
    logger.warning(
        "unstructured_llm_output | reason={}",
        "unstructured_llm_output",
    )
    sentences = raw_text.replace("\n", " ").split(".")
    summary = ". ".join(s.strip() for s in sentences[:2] if s.strip())
    if summary and not summary.endswith("."):
        summary += "."
    return (summary[:1200], [], [], [])


def _split_list_section(section_text: str) -> list[str]:
    """Split a section into list items by newlines or bullet points."""
    if not section_text:
        return []
    lines = section_text.strip().split("\n")
    items: list[str] = []
    for line in lines:
        cleaned = line.strip().lstrip("-•*").strip()
        if cleaned:
            items.append(cleaned)
    return items


def _check_score_range(score: int) -> bool:
    """Signal score must be in [0, 100]."""
    return 0 <= score <= 100


def _check_confidence_range(confidence: Decimal) -> bool:
    """Confidence must be in [0.0, 1.0]."""
    return Decimal("0.0") <= confidence <= Decimal("1.0")


def _check_raw_score_cap(total: int) -> bool:
    """Category scores total must be <= 220."""
    return total <= 220


def _check_telemetry_present(telemetry: object | None) -> bool:
    """Telemetry must not be None."""
    return telemetry is not None


def _check_schema_v2_fields(signal: SignalOutput) -> bool:
    """Verify asset, decision, and expires_at are populated."""
    if not signal.asset:
        return False
    if signal.decision is None:
        return False
    if signal.expires_at is None:
        return False
    return True


def _build_activity_record(
    processed: ProcessedSignal,
) -> dict[str, object]:
    """Build a minimal activity record for the frontend stream."""
    return {
        "signal_id": processed.signal_id,
        "asset": processed.asset,
        "decision": processed.decision,
        "score": processed.signal.score,
        "model_used": processed.model_used,
        "reasoning_summary": processed.reasoning_summary[:200],
        "timestamp": processed.cycle_timestamp.isoformat(),
        "emitted": processed.emit_to_prometheus,
    }
