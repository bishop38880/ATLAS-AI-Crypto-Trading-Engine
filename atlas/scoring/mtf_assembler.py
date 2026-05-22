"""
Assembles a complete MTFSignalPacket from three per-timeframe
compact v2.1 signal packages.
The 15m package is used as the 'entry' package — its agent breakdown
and HYDRA context are the ones passed to the LLM. The 4h and 30m
packages contribute scores only.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from atlas.models.mtf import MTFSignalPacket
from atlas.scoring.mtf_scorer import compute_mtf_block


def _total_score(pkg: dict[str, Any]) -> int:
    scores = pkg["scores"]
    if not isinstance(scores, dict):
        raise TypeError("scores must be a dict")
    raw_total = scores.get("total")
    if raw_total is None:
        raise KeyError("scores.total missing")
    return int(raw_total)


def assemble_mtf_packet(
    pkg_4h: dict[str, Any],
    pkg_30m: dict[str, Any],
    pkg_15m: dict[str, Any],
) -> MTFSignalPacket:
    """
    Takes three compact v2.1 packages (one per timeframe) and assembles
    a single MTFSignalPacket for LLM dispatch.
    Score extraction: pkg["scores"]["total"] for each timeframe.
    Timestamp extraction: pkg["ts"] for each timeframe.
    The 15m package is the 'entry' package — its agents, HYDRA, and
    flags are what the LLM reasons over. The 4h and 30m contribute
    scores and timestamps only.
    """
    sc_4h = _total_score(pkg_4h)
    sc_30m = _total_score(pkg_30m)
    sc_15m = _total_score(pkg_15m)
    ts_4h = str(pkg_4h["ts"])
    ts_30m = str(pkg_30m["ts"])
    ts_15m = str(pkg_15m["ts"])
    symbol = str(pkg_15m["sym"])

    mtf_block = compute_mtf_block(
        sc_4h=sc_4h,
        sc_30m=sc_30m,
        sc_15m=sc_15m,
        ts_4h=ts_4h,
        ts_30m=ts_30m,
        ts_15m=ts_15m,
    )

    logger.info(
        "mtf_packet_assembled | sym={} avg={} alignment={} stale={}",
        symbol,
        mtf_block.avg,
        mtf_block.alignment,
        mtf_block.is_stale,
    )

    return MTFSignalPacket(
        symbol=symbol,
        mode="CONFLUENCE",
        mtf=mtf_block,
        package=pkg_15m,
    )
