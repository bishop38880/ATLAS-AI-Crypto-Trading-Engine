"""
Multi-timeframe confluence scoring endpoint.
Accepts three compact v2.1 packages (4h, 30m, 15m),
assembles the MTF packet, scores it, dispatches to LLM,
and returns the structured decision.
"""

from __future__ import annotations

import asyncio
from typing import Any

import msgspec
import msgspec.json
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from loguru import logger

from atlas.core.dependencies import get_local_llm
from atlas.core.llm_client import LocalLLMClient, LocalLLMUnavailableError
from atlas.llm.response_parser import extract_json_from_response
from atlas.models.mtf import MTFSignalPacket
from atlas.prompts.mtf_confluence_prompt import MTF_CONFLUENCE_SYSTEM_PROMPT
from atlas.scoring.mtf_assembler import assemble_mtf_packet

router = APIRouter(prefix="/api/mtf", tags=["mtf-confluence"])


class MTFRequest(msgspec.Struct, frozen=True):
    """Three compact v2.1 packages — one per timeframe."""

    pkg_4h: dict[str, Any]
    pkg_30m: dict[str, Any]
    pkg_15m: dict[str, Any]


class MTFResponse(msgspec.Struct, frozen=True):
    symbol: str
    mtf_avg: int
    alignment: str
    decision: str
    direction: str
    signal_type: str
    confidence: str
    size_modifier: str
    hydra_note: str
    key_flags: list[str]
    skip_reason: str | None
    is_stale: bool
    stale_tf: str | None


def _json_response(body: MTFResponse) -> Response:
    return Response(content=msgspec.json.encode(body), media_type="application/json")


def _encode_user_payload(package: dict[str, Any], mtf_view: dict[str, Any]) -> str:
    merged: dict[str, Any] = dict(package)
    merged["mtf"] = mtf_view
    merged["mode"] = "CONFLUENCE"
    return msgspec.json.encode(merged).decode()


def _mtf_view_from_packet(packet: MTFSignalPacket) -> dict[str, Any]:
    return {
        "sc_4h": packet.mtf.sc_4h,
        "sc_30m": packet.mtf.sc_30m,
        "sc_15m": packet.mtf.sc_15m,
        "base_avg": packet.mtf.base_avg,
        "alignment": packet.mtf.alignment,
        "multiplier": str(packet.mtf.multiplier),
        "avg": packet.mtf.avg,
        "is_stale": False,
    }


def _stale_http_response(packet: MTFSignalPacket) -> Response:
    label = packet.mtf.stale_tf
    logger.info("mtf_stale_short_circuit | sym={} stale_tf={}", packet.symbol, label)
    body = MTFResponse(
        symbol=packet.symbol,
        mtf_avg=0,
        alignment=str(packet.mtf.alignment),
        decision="NO_TRADE",
        direction="NONE",
        signal_type="STALE_SIGNAL",
        confidence="none",
        size_modifier="zero",
        hydra_note="",
        key_flags=[],
        skip_reason=None if label is None else "Stale timeframe: {}".format(label),
        is_stale=True,
        stale_tf=label,
    )
    return _json_response(body)


def _flatten_key_flags(flags_raw: object) -> list[str]:
    if not isinstance(flags_raw, list):
        return []
    return [str(item) for item in flags_raw]


def _parse_llm_json(symbol: str, text: str) -> dict[str, Any]:
    try:
        json_str = extract_json_from_response(text)
        return msgspec.json.decode(json_str.encode(), type=dict)
    except Exception as exc:
        logger.error("llm_parse_error | sym={} error={}", symbol, exc)
        raise HTTPException(
            status_code=500,
            detail="LLM response parse failed: {}".format(exc),
        )


async def _read_mtf_packet(request: Request) -> MTFSignalPacket:
    try:
        req_body = msgspec.json.decode(await request.body(), type=MTFRequest)
    except msgspec.DecodeError as exc:
        raise HTTPException(status_code=422, detail="Invalid MTF JSON: {}".format(exc))
    try:
        return assemble_mtf_packet(req_body.pkg_4h, req_body.pkg_30m, req_body.pkg_15m)
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Invalid package format: {}".format(exc))


def _skip_reason_from_decoded(skip_val: object) -> str | None:
    if skip_val is None:
        return None
    if isinstance(skip_val, str):
        return skip_val
    return str(skip_val)


def _success_body(packet: MTFSignalPacket, decoded: dict[str, Any]) -> MTFResponse:
    return MTFResponse(
        symbol=packet.symbol,
        mtf_avg=packet.mtf.avg,
        alignment=str(packet.mtf.alignment),
        decision=str(decoded.get("decision", "NO_TRADE")),
        direction=str(decoded.get("direction", "NONE")),
        signal_type=str(decoded.get("signal_type", "CONFLUENCE")),
        confidence=str(decoded.get("confidence", "none")),
        size_modifier=str(decoded.get("size_modifier", "zero")),
        hydra_note=str(decoded.get("hydra_note", "")),
        key_flags=_flatten_key_flags(decoded.get("key_flags", [])),
        skip_reason=_skip_reason_from_decoded(decoded.get("skip_reason")),
        is_stale=False,
        stale_tf=None,
    )


@router.post("/score", response_model=None)
async def score_mtf_confluence(
    request: Request,
    llm: LocalLLMClient = Depends(get_local_llm),
) -> Response:
    """Assemble MTF fusion; stale short-circuit; else complete and decode JSON."""
    pkt = await _read_mtf_packet(request)
    if pkt.mtf.is_stale:
        return _stale_http_response(pkt)

    usr = _encode_user_payload(pkt.package, _mtf_view_from_packet(pkt))

    try:
        ans = await llm.complete_confluence_turn(
            system_prompt=MTF_CONFLUENCE_SYSTEM_PROMPT,
            user_message=usr,
        )
    except LocalLLMUnavailableError:
        logger.warning("local_llm_unavailable | sym={}", pkt.symbol)
        raise HTTPException(
            status_code=503,
            detail="Local LLM unavailable. Check LM Studio is running.",
        )
    except asyncio.CancelledError:
        raise

    decoded = _parse_llm_json(pkt.symbol, ans.text.strip())
    logger.info(
        "mtf_decision | sym={} avg={} alignment={} decision={} direction={}",
        pkt.symbol,
        pkt.mtf.avg,
        pkt.mtf.alignment,
        decoded.get("decision"),
        decoded.get("direction"),
    )
    return _json_response(_success_body(pkt, decoded))
