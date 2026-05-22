import re
from enum import Enum
from datetime import datetime, timezone, timedelta
from typing import ClassVar, Any
from pydantic import BaseModel, Field
from loguru import logger

class VerificationStatus(str, Enum):
    CLEAN          = "clean"
    FLAGGED        = "flagged"
    CONTRADICTED   = "contradicted"
    INCOHERENT     = "incoherent"
    TIMESTAMP_ANOMALY = "timestamp_anomaly"
    DUPLICATE      = "duplicate"

class VerificationFlag(BaseModel, frozen=True):
    """One individual flag raised during verification."""
    rule_id: str = Field(description="Short identifier for the rule that fired")
    severity: str = Field(description="'warn' or 'critical'")
    description: str = Field(description="Human-readable description of what was found")
    evidence: dict[str, str] = Field(description="Specific values that triggered the rule")

class ContradictionPair(BaseModel, frozen=True):
    """Record of a cross-document contradiction."""
    existing_doc_id: str = Field(description="Document ID of the conflicting existing document")
    existing_timestamp: datetime = Field(description="When the conflicting document was written")
    existing_decision: str = Field(description="The decision in the conflicting document")
    existing_score: int = Field(description="Score of the conflicting document")
    time_delta_minutes: float = Field(description="How many minutes apart the two documents are")
    contradiction_type: str = Field(description="'direction_flip' | 'score_cliff' | 'regime_mismatch'")

class VerifierReport(BaseModel, frozen=True):
    """Complete verification report for one document."""
    document_id: str = Field(description="The document being verified")
    status: VerificationStatus = Field(description="Overall verification status")
    flags: tuple[VerificationFlag, ...] = Field(description="All individual flags raised")
    contradictions: tuple[ContradictionPair, ...] = Field(description="Cross-document contradiction records")
    verified_at: datetime = Field(description="When verification ran")
    duration_ms: float = Field(description="How long verification took")
    contradiction_note: str = Field(description="Human-readable summary for LLM context injection; empty when CLEAN")

class DocumentVerifier:
    """Deterministic verification of RAG memory documents before storage."""

    DIRECTION_MIN_KEYWORDS = 3

    BULLISH_KEYWORDS: frozenset[str] = frozenset({
        "bullish", "buy", "accumulation", "upward", "rally", "breakout",
        "positive", "inflow", "rising", "oversold", "support", "upside",
    })

    BEARISH_KEYWORDS: frozenset[str] = frozenset({
        "bearish", "sell", "distribution", "dump", "downward", "decline",
        "breakdown", "negative", "outflow", "falling", "overbought",
        "resistance", "rejection", "downside",
    })

    CONTRADICTION_MIN_SCORE = 140
    CONTRADICTION_WINDOW_HOURS = 4
    SCORE_CLIFF_MIN_DELTA = 60
    SCORE_CLIFF_WINDOW_MINUTES = 30

    _TOKEN_RE: ClassVar[re.Pattern[str]] = re.compile(r"\b\w+\b")
    _BULLISH_DECISIONS: ClassVar[frozenset[str]] = frozenset({"Strong Buy", "Buy"})
    _BEARISH_DECISIONS: ClassVar[frozenset[str]] = frozenset({"Strong Sell", "Sell"})

    def __init__(
        self,
        qdrant_client: Any,
        qdrant_collection: str,
        redis_client: Any,
    ) -> None:
        self._qdrant = qdrant_client
        self._collection = qdrant_collection
        self._redis = redis_client

    async def verify(self, document: Any) -> VerifierReport:
        start_time = datetime.now(timezone.utc)
        doc_id = getattr(document, "id", "unknown")
        try:
            flags, contradictions = await self._run_all_checks(document)
            status = self._determine_status(flags, contradictions)
            note = self._build_contradiction_note(flags, contradictions)
            duration = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000.0
            return VerifierReport(
                document_id=str(doc_id), status=status,
                flags=tuple(flags), contradictions=tuple(contradictions),
                verified_at=datetime.now(timezone.utc),
                duration_ms=duration, contradiction_note=note,
            )
        except Exception as exc:
            return self._handle_verify_error(doc_id, exc, start_time)

    async def _run_all_checks(
        self, document: Any,
    ) -> tuple[list[VerificationFlag], list[ContradictionPair]]:
        """Execute all rule sets and return flags + contradictions."""
        flags: list[VerificationFlag] = []
        for check in (
            self._check_timestamp_epoch, self._check_timestamp_future,
            self._check_required_fields, self._check_score_range,
            self._check_key_lists_populated,
            self._check_reasoning_convergences_coherence,
            self._check_decision_score_alignment,
        ):
            f = check(document)
            if f:
                flags.append(f)
        contradictions: list[ContradictionPair] = []
        c_pair, f_dir = await self._check_direction_contradiction(document)
        if c_pair and f_dir:
            contradictions.append(c_pair)
            flags.append(f_dir)
        f_cliff = await self._check_score_cliff(document)
        if f_cliff:
            flags.append(f_cliff)
        return flags, contradictions

    def _handle_verify_error(
        self, doc_id: str, exc: Exception, start_time: datetime,
    ) -> VerifierReport:
        """Build error report on internal verification failure."""
        logger.error("Internal error during verification document_id={} | exc={}", doc_id, exc)
        err_flag = VerificationFlag(
            rule_id="RULE_INTERNAL_ERROR", severity="critical",
            description="Internal error: {}".format(exc),
            evidence={"error": str(exc)},
        )
        duration = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000.0
        return VerifierReport(
            document_id=str(doc_id), status=VerificationStatus.FLAGGED,
            flags=(err_flag,), contradictions=(),
            verified_at=datetime.now(timezone.utc),
            duration_ms=duration, contradiction_note="",
        )

    def _check_timestamp_epoch(self, document: Any) -> VerificationFlag | None:
        EARLIEST_VALID = datetime(2023, 1, 1, tzinfo=timezone.utc)
        ts = getattr(document, "timestamp", EARLIEST_VALID)
        if ts < EARLIEST_VALID:
            return VerificationFlag(
                rule_id="RULE_TIMESTAMP_EPOCH",
                severity="critical",
                description=f"Document timestamp {ts.isoformat()} predates 2023-01-01",
                evidence={"timestamp": ts.isoformat(), "threshold": EARLIEST_VALID.isoformat()},
            )
        return None

    def _check_timestamp_future(self, document: Any) -> VerificationFlag | None:
        now = datetime.now(timezone.utc)
        ts = getattr(document, "timestamp", now)
        if ts > now + timedelta(minutes=5):
            return VerificationFlag(
                rule_id="RULE_TIMESTAMP_FUTURE",
                severity="warn",
                description=f"Document timestamp {ts.isoformat()} is in the future",
                evidence={"timestamp": ts.isoformat(), "now": now.isoformat(), "delta_minutes": str((ts - now).total_seconds() / 60)},
            )
        return None

    def _check_required_fields(self, document: Any) -> VerificationFlag | None:
        required = {
            "reasoning_summary": getattr(document, "reasoning_summary", None),
            "asset": getattr(document, "asset", None),
            "decision": getattr(document, "decision", None),
        }
        missing = [k for k, v in required.items() if not v or not str(v).strip()]
        if missing:
            return VerificationFlag(
                rule_id="RULE_MISSING_FIELDS",
                severity="critical",
                description=f"Required fields empty or missing: {missing}",
                evidence={"missing_fields": str(missing)},
            )
        return None

    def _check_score_range(self, document: Any) -> VerificationFlag | None:
        score = getattr(document, "confluence_score", None)
        if score is not None and not (0 <= score <= 220):
            return VerificationFlag(
                rule_id="RULE_SCORE_RANGE",
                severity="critical",
                description=f"Score {score} outside valid range 0-220",
                evidence={"score": str(score)},
            )
        return None

    def _check_key_lists_populated(self, document: Any) -> VerificationFlag | None:
        convergences = getattr(document, "key_convergences", []) or []
        risks = getattr(document, "key_risks", []) or []
        if not convergences and not risks:
            return VerificationFlag(
                rule_id="RULE_EMPTY_ANALYSIS",
                severity="warn",
                description="Both key_convergences and key_risks are empty - likely Output Processor parsing failure",
                evidence={"convergences_count": "0", "risks_count": "0"},
            )
        return None

    def _count_directional_keywords(self, text: str) -> tuple[int, int]:
        tokens = set(self._TOKEN_RE.findall(text.lower()))
        bullish = len(tokens & self.BULLISH_KEYWORDS)
        bearish = len(tokens & self.BEARISH_KEYWORDS)
        return bullish, bearish

    def _direction_from_decision(self, decision: str) -> str | None:
        if decision in self._BULLISH_DECISIONS:
            return "bullish"
        if decision in self._BEARISH_DECISIONS:
            return "bearish"
        return None

    def _infer_direction(self, text: str) -> str:
        bullish, bearish = self._count_directional_keywords(text)
        if bullish < self.DIRECTION_MIN_KEYWORDS and bearish < self.DIRECTION_MIN_KEYWORDS:
            return "neutral"
        if abs(bullish - bearish) <= 1:
            return "neutral"
        return "bullish" if bullish > bearish else "bearish"

    def _check_reasoning_convergences_coherence(self, document: Any) -> VerificationFlag | None:
        reasoning = getattr(document, "reasoning_summary", "") or ""
        convergences = " ".join(getattr(document, "key_convergences", []) or [])
        r_direction = self._infer_direction(reasoning)
        c_direction = self._infer_direction(convergences)

        if r_direction == "neutral" or c_direction == "neutral":
            return None
        if r_direction != c_direction:
            r_bull, r_bear = self._count_directional_keywords(reasoning)
            c_bull, c_bear = self._count_directional_keywords(convergences)
            return VerificationFlag(
                rule_id="RULE_INCOHERENT_DIRECTION",
                severity="critical",
                description=(
                    f"REASONING direction ({r_direction}: bull={r_bull}, bear={r_bear}) "
                    f"contradicts CONVERGENCES direction ({c_direction}: bull={c_bull}, bear={c_bear})"
                ),
                evidence={
                    "reasoning_direction": r_direction,
                    "convergences_direction": c_direction,
                    "reasoning_bull": str(r_bull),
                    "reasoning_bear": str(r_bear),
                    "convergences_bull": str(c_bull),
                    "convergences_bear": str(c_bear),
                },
            )
        return None

    def _check_decision_score_alignment(self, document: Any) -> VerificationFlag | None:
        score = getattr(document, "confluence_score", None)
        decision = getattr(document, "decision", None)
        if score is None or decision is None:
            return None
        expected = self._expected_tier(score)
        decision_norm = str(decision).strip()
        unknown_flag = self._check_unknown_decision(decision_norm)
        if unknown_flag:
            return unknown_flag
        canonical = decision_norm.replace("_", " ").title()
        if canonical not in expected:
            return VerificationFlag(
                rule_id="RULE_DECISION_SCORE_MISMATCH", severity="critical",
                description="Decision '{}' inconsistent with score {} (expected: {})".format(
                    canonical, score, sorted(expected)),
                evidence={"decision": canonical, "score": str(score), "expected": str(sorted(expected))},
            )
        return None

    @staticmethod
    def _expected_tier(score: int) -> set[str]:
        """Map score to expected decision tier."""
        if score >= 180: return {"Strong Buy", "Strong Sell"}
        if score >= 150: return {"Strong Buy", "Strong Sell", "Buy", "Sell"}
        if score >= 120: return {"Hold"}
        return {"No Position"}

    @staticmethod
    def _check_unknown_decision(decision_norm: str) -> VerificationFlag | None:
        """Return a flag if the decision is not a known value."""
        valid = {
            "Strong Buy", "Buy", "Hold", "Sell", "Strong Sell", "No Position",
            "STRONG_BUY", "BUY", "HOLD", "SELL", "STRONG_SELL", "NO_POSITION",
        }
        if decision_norm not in valid:
            return VerificationFlag(
                rule_id="RULE_UNKNOWN_DECISION", severity="critical",
                description="Unknown decision value: '{}'".format(decision_norm),
                evidence={"decision": decision_norm},
            )
        return None

    async def _fetch_recent_qdrant_docs(self, asset: str, min_score: int, window_start: datetime, ts: datetime) -> list[Any]:
        from qdrant_client.models import Filter, FieldCondition, MatchValue, Range, DatetimeRange
        try:
            res = await self._qdrant.scroll(
                collection_name=self._collection,
                scroll_filter=Filter(must=[
                    FieldCondition(key="asset", match=MatchValue(value=asset)),
                    FieldCondition(key="confluence_score", range=Range(gte=min_score)),
                    FieldCondition(key="timestamp", range=DatetimeRange(gte=window_start, lte=ts)), # type: ignore[arg-type]
                ]),
                limit=10,
                with_payload=True,
            )
            return res[0] if res else []
        except Exception as exc:
            logger.warning("Qdrant contradiction scan failed | exc={}", exc)
            return []

    async def _check_direction_contradiction(
        self, document: Any
    ) -> tuple[ContradictionPair | None, VerificationFlag | None]:
        score = getattr(document, "confluence_score", 0)
        if score < self.CONTRADICTION_MIN_SCORE:
            return None, None
        decision = str(getattr(document, "decision", "")).replace("_", " ").title()
        asset = getattr(document, "asset", "")
        ts = getattr(document, "timestamp", datetime.now(timezone.utc))
        window_start = ts - timedelta(hours=self.CONTRADICTION_WINDOW_HOURS)
        new_dir = self._direction_from_decision(decision)
        if new_dir is None:
            return None, None
        points = await self._fetch_recent_qdrant_docs(asset, self.CONTRADICTION_MIN_SCORE, window_start, ts)
        if not points:
            return None, None
        for point in points:
            result = self._match_contradicting_point(point, new_dir, score, ts)
            if result:
                return result
        return None, None

    def _match_contradicting_point(
        self, point: Any, new_dir: str, score: int, ts: datetime,
    ) -> tuple[ContradictionPair, VerificationFlag] | None:
        """Check if a single point contradicts the new document."""
        payload = point.payload or {}
        existing_dec = str(payload.get("decision", "")).replace("_", " ").title()
        existing_dir = self._direction_from_decision(existing_dec)
        if existing_dir is None or existing_dir == new_dir:
            return None
        existing_score = int(payload.get("confluence_score", 0))
        if existing_score < self.CONTRADICTION_MIN_SCORE:
            return None
        existing_ts = self._parse_ts(payload.get("timestamp", ""))
        if existing_ts is None:
            return None
        delta_minutes = abs((ts - existing_ts).total_seconds() / 60)
        pair = ContradictionPair(
            existing_doc_id=str(point.id), existing_timestamp=existing_ts,
            existing_decision=existing_dec, existing_score=existing_score,
            time_delta_minutes=delta_minutes, contradiction_type="direction_flip",
        )
        flag = self._build_direction_flag(new_dir, score, point.id, existing_dir, existing_score, delta_minutes)
        return pair, flag

    @staticmethod
    def _parse_ts(ts_str: str) -> datetime | None:
        """Parse ISO timestamp with UTC fallback."""
        try:
            ts = datetime.fromisoformat(ts_str)
            return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _build_direction_flag(
        new_dir: str, score: int, point_id: Any,
        existing_dir: str, existing_score: int, delta_minutes: float,
    ) -> VerificationFlag:
        """Build contradiction flag."""
        return VerificationFlag(
            rule_id="RULE_CROSS_DIRECTION_CONTRADICTION", severity="critical",
            description="New document ({}, score {}) contradicts document {} ({}, score {}, {:.0f}min ago)".format(
                new_dir, score, point_id, existing_dir, existing_score, delta_minutes),
            evidence={
                "new_direction": new_dir, "existing_direction": existing_dir,
                "new_score": str(score), "existing_score": str(existing_score),
                "existing_doc_id": str(point_id), "delta_minutes": "{:.1f}".format(delta_minutes),
            },
        )

    async def _check_score_cliff(self, document: Any) -> VerificationFlag | None:
        score = getattr(document, "confluence_score", None)
        asset = getattr(document, "asset", "")
        ts = getattr(document, "timestamp", datetime.now(timezone.utc))
        if score is None:
            return None
        points = await self._fetch_score_cliff_points(asset, ts)
        if not points:
            return None
        for point in points:
            existing_score = int((point.payload or {}).get("confluence_score", 0))
            delta = abs(score - existing_score)
            if delta >= self.SCORE_CLIFF_MIN_DELTA:
                return self._build_cliff_flag(score, existing_score, delta)
        return None

    async def _fetch_score_cliff_points(self, asset: str, ts: datetime) -> list[Any]:
        """Scroll Qdrant for recent documents to check score cliffs."""
        window_start = ts - timedelta(minutes=self.SCORE_CLIFF_WINDOW_MINUTES)
        try:
            results = await self._qdrant.scroll(
                collection_name=self._collection,
                scroll_filter={"must": [
                    {"key": "asset", "match": {"value": asset}},
                    {"key": "timestamp", "range": {
                        "gte": window_start.isoformat(), "lte": ts.isoformat(),
                    }},
                ]},
                limit=5, with_payload=True,
            )
            return results[0] if results else []
        except Exception as exc:
            logger.warning("Qdrant score cliff scan failed | exc={}", exc)
            return []

    def _build_cliff_flag(
        self, new_score: int, existing_score: int, delta: int,
    ) -> VerificationFlag:
        """Build a score cliff warning flag."""
        return VerificationFlag(
            rule_id="RULE_SCORE_CLIFF", severity="warn",
            description="Score cliff detected: {} -> {} (\u0394{} pts within {}min)".format(
                existing_score, new_score, delta, self.SCORE_CLIFF_WINDOW_MINUTES),
            evidence={
                "new_score": str(new_score), "existing_score": str(existing_score),
                "delta": str(delta), "window_minutes": str(self.SCORE_CLIFF_WINDOW_MINUTES),
            },
        )

    def _build_contradiction_note(
        self, flags: list[VerificationFlag], contradictions: list[ContradictionPair]
    ) -> str:
        critical = [f for f in flags if f.severity == "critical"]
        if not critical and not contradictions:
            return ""

        parts = ["[VERIFICATION NOTE]"]
        for flag in critical:
            parts.append(f"  {flag.rule_id}: {flag.description}")
        for pair in contradictions:
            parts.append(
                f"  CONTRADICTION: Conflicts with document {pair.existing_doc_id} "
                f"({pair.existing_decision}, score {pair.existing_score}, "
                f"{pair.time_delta_minutes:.0f}min apart)"
            )
        parts.append("[/VERIFICATION NOTE]")
        return "\n".join(parts)

    def _determine_status(
        self, flags: list[VerificationFlag], contradictions: list[ContradictionPair]
    ) -> VerificationStatus:
        if contradictions:
            return VerificationStatus.CONTRADICTED

        rule_ids = {f.rule_id for f in flags}

        if "RULE_TIMESTAMP_EPOCH" in rule_ids:
            return VerificationStatus.TIMESTAMP_ANOMALY
        if "RULE_INCOHERENT_DIRECTION" in rule_ids:
            return VerificationStatus.INCOHERENT

        critical_flags = [f for f in flags if f.severity == "critical"]
        if critical_flags:
            return VerificationStatus.FLAGGED
        if flags:
            return VerificationStatus.FLAGGED

        return VerificationStatus.CLEAN

    async def _scan_for_opposite_signals(
        self, asset: str, new_direction: str, ts: datetime
    ) -> list[Any]:
        from qdrant_client.models import Filter, FieldCondition, MatchValue, Range, DatetimeRange

        window_start = ts - timedelta(days=14)
        try:
            res = await self._qdrant.scroll(
                collection_name=self._collection,
                scroll_filter=Filter(must=[
                    FieldCondition(key="asset", match=MatchValue(value=asset)),
                    FieldCondition(key="document_state", match=MatchValue(value="verified")),
                    FieldCondition(key="confluence_score", range=Range(gte=self.CONTRADICTION_MIN_SCORE)),
                    FieldCondition(key="timestamp", range=DatetimeRange(gte=window_start, lte=ts)), # type: ignore[arg-type]
                ]),
                limit=100,
                with_payload=True,
            )
            return res[0] if res else []
        except Exception as exc:
            logger.warning("Qdrant opposite signal scan failed | exc={}", exc)
            return []

    async def check_and_record_contradictions(
        self, document: Any, report: VerifierReport, state_machine: Any
    ) -> None:
        from atlas.shared.config import PolarisSettings
        if not PolarisSettings().recursive_verify_enabled:
            return

        if report.status != VerificationStatus.CLEAN:
            return

        decision = str(getattr(document, "decision", "")).replace("_", " ").title()
        new_dir = self._direction_from_decision(decision)
        if new_dir is None:
            return

        asset = getattr(document, "asset", "")
        ts = getattr(document, "timestamp", datetime.now(timezone.utc))
        score = getattr(document, "confluence_score", 0)

        points = await self._scan_for_opposite_signals(asset, new_dir, ts)
        for point in points:
            await self._process_opposite_signal(
                document, point, new_dir, decision, asset, score, ts, state_machine
            )

    async def _process_opposite_signal(
        self, document: Any, point: Any, new_dir: str, decision: str, asset: str, score: int, ts: datetime, state_machine: Any
    ) -> None:
        from atlas.rag.state_machine import ContradictionAccumulation
        from decimal import Decimal
        payload = point.payload or {}
        existing_dec = str(payload.get("decision", "")).replace("_", " ").title()
        existing_dir = self._direction_from_decision(existing_dec)

        if existing_dir is None or existing_dir == new_dir:
            return

        existing_score = int(payload.get("confluence_score", 0))
        try:
            existing_ts = datetime.fromisoformat(payload.get("timestamp", ""))
            if existing_ts.tzinfo is None:
                existing_ts = existing_ts.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return

        delta_days = Decimal(str(abs((ts - existing_ts).total_seconds()))) / Decimal("86400")
        accum = ContradictionAccumulation(
            target_document_id=str(point.id),
            source_document_id=str(getattr(document, "id", "")),
            asset=asset,
            target_decision=existing_dec,
            source_decision=decision,
            target_score=existing_score,
            source_score=score,
            time_delta_days=delta_days,
        )
        await state_machine.record_contradiction(accum)
