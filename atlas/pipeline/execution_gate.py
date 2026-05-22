"""Execution-path gating helpers — fail-fast score checks before live fan-out."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from loguru import logger


def log_skipped_opportunity(
    *,
    asset: str,
    timestamp: datetime,
    score: int,
    reason: str,
    min_trade_score: int,
    raw_confluence_score: int | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Lightweight structured log for sub-threshold setups (no embeddings / heavy I/O).

    Args:
        asset: Trading symbol as used in the pipeline.
        timestamp: Signal or evaluation time (UTC).
        score: Normalised confluence score (0–100) that failed the gate.
        reason: Machine-oriented rejection label for backtesting joins.
        min_trade_score: Configured ``PolarisSettings.min_trade_score``.
        raw_confluence_score: Optional raw 220-point score for diagnostics.
        extra: Optional small key-value bag (kept tiny for log volume).
    """
    ts_iso = timestamp.astimezone(timezone.utc).isoformat()
    raw_part = (
        " | raw_confluence_score={}".format(raw_confluence_score)
        if raw_confluence_score is not None
        else ""
    )
    extra_part = ""
    if extra:
        pairs = " | ".join(
            "{}={}".format(str(k), str(v)) for k, v in list(extra.items())[:8]
        )
        if pairs:
            extra_part = " | {}".format(pairs)
    logger.info(
        "skipped_opportunity | asset={} | ts={} | score={} | min_trade_score={}"
        " | reason={}{}{}",
        asset,
        ts_iso,
        score,
        min_trade_score,
        reason,
        raw_part,
        extra_part,
    )
