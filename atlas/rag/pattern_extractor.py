"""Nightly pattern extraction and consolidation."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg  # type: ignore[import-untyped]
import msgspec
from loguru import logger
from pydantic import BaseModel, Field

from atlas.core.llm_client import DeepSeekClient
from atlas.rag.writer import RAGWriter


class ExtractedPattern(BaseModel, frozen=True):
    """Schema for a single generalized macro-lesson extracted by DeepSeek."""

    pattern_type: str = Field(description="Short title or type of pattern")
    content_text: str = Field(description="The core lesson or generalization")
    regime: str = Field(description="Market regime this pattern applies to")
    ttl_hours: int = Field(description="How long this pattern remains relevant")


class OutcomeAggregator:
    """Aggregates recent trading outcomes for analysis."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def fetch_recent_outcomes(self, days: int = 7) -> dict[str, list[dict[str, Any]]]:
        """Fetch closed trades from the last N days, grouped by regime and label."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        records = await self._pool.fetch(
            """
            SELECT signal_id, asset, timeframe, decision, outcome_label,
                   pnl_pct, exit_reason, metadata->>'regime' as regime,
                   reasoning
            FROM signal_history
            WHERE outcome_at >= $1
              AND outcome_label IS NOT NULL
            """,
            cutoff,
            timeout=15.0,
        )
        return self._group_records(records)

    def _group_records(self, records: list[asyncpg.Record]) -> dict[str, list[dict[str, Any]]]:
        """Group records by regime and outcome label."""
        grouped: dict[str, list[dict[str, Any]]] = {}
        for r in records:
            regime = r["regime"] or "unknown"
            label = r["outcome_label"]
            key = f"{regime}_{label}"
            if key not in grouped:
                grouped[key] = []
            grouped[key].append(dict(r))
        return grouped


class PatternSynthesiser:
    """Uses DeepSeek to extract generalized lessons and stores them in RAG memory."""

    def __init__(self, llm_client: DeepSeekClient, rag_writer: RAGWriter) -> None:
        self._llm = llm_client
        self._writer = rag_writer

    async def run_dream_cycle(self, aggregator: OutcomeAggregator, days: int = 7) -> None:
        """Run the full dream cycle pipeline."""
        logger.info("Starting Dream Cycle...")
        grouped = await aggregator.fetch_recent_outcomes(days)
        patterns = await self.synthesize_patterns(grouped)
        if patterns:
            await self.store_extracted_patterns(patterns)
        logger.info("Dream Cycle completed. Extracted {} patterns.", len(patterns))

    async def synthesize_patterns(
        self, grouped_outcomes: dict[str, list[dict[str, Any]]]
    ) -> list[ExtractedPattern]:
        """Prompt DeepSeek to analyze trades and extract lessons."""
        if not grouped_outcomes:
            return []
            
        prompt = self._build_prompt(grouped_outcomes)
        try:
            resp = await self._llm.complete(prompt, max_tokens=1500)
            return self._parse_patterns(resp.text)
        except Exception as e:
            logger.error("Failed to synthesize patterns: {}", e)
            return []

    def _build_prompt(self, grouped_outcomes: dict[str, list[dict[str, Any]]]) -> str:
        """Construct the prompt for DeepSeek reasoner."""
        data_str = msgspec.json.encode(grouped_outcomes).decode("utf-8")
        return (
            "You are a quantitative risk manager. Analyze this batch of recent trades. "
            "Identify common denominators in the losing trades and winning trades. "
            "Extract 1-3 generalized macro-lessons.\n"
            "Output strict JSON as a list of objects with keys:\n"
            "pattern_type, content_text, regime, ttl_hours.\n\n"
            f"Trades: {data_str}"
        )

    def _parse_patterns(self, text: str) -> list[ExtractedPattern]:
        """Parse JSON response into ExtractedPattern models."""
        try:
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0].strip()
            elif "```" in text:
                text = text.split("```")[1].split("```")[0].strip()
                
            data = msgspec.json.decode(text.encode("utf-8"))
            if not isinstance(data, list):
                logger.error("DeepSeek returned non-list JSON for patterns")
                return []
                
            return self._validate_patterns(data)
        except Exception as e:
            logger.error("Failed to parse patterns JSON: {}", e)
            return []

    def _validate_patterns(self, data: list[Any]) -> list[ExtractedPattern]:
        """Validate and construct ExtractedPattern from list."""
        patterns = []
        for item in data:
            try:
                patterns.append(ExtractedPattern(**item))
            except Exception as e:
                logger.warning("Skipping invalid pattern: {}", e)
        return patterns

    async def store_extracted_patterns(self, patterns: list[ExtractedPattern]) -> None:
        """Store patterns using the RAGWriter."""
        for p in patterns:
            try:
                await self._writer.write_pattern(
                    title=p.pattern_type,
                    content=p.content_text,
                    category=p.regime,
                    source="dream_cycle",
                )
            except Exception as e:
                logger.error("Failed to store pattern '{}': {}", p.pattern_type, e)
