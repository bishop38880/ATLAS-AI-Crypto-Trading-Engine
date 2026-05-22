"""
FastMCP server for Prediction Market intelligence.

Section 5 Architecture: Macro Context — Capital-Weighted Probabilities.
Exposes four tools for the NewsCatalystAgent and MacroCrossMarketAgent:
  - search_macro_events
  - get_orderbook_probability
  - get_fed_rate_implied_path
  - get_regulatory_risk_matrix

Sentinel Invariants:
  - msgspec for all JSON serialization
  - httpx.AsyncClient (singleton per client)
  - asyncio.to_thread for CPU-bound work
  - Loguru positional format only
  - No os.getenv — uses dotenv for MCP-scoped config
  - Max 40 lines per function
"""

from __future__ import annotations

import asyncio
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

import msgspec
from dotenv import dotenv_values
from loguru import logger
from mcp.server.fastmcp import FastMCP

from .clients.kalshi_client import KalshiClient
from .clients.polymarket_client import PolymarketClient
from .models import (
    FedRateMeeting,
    FedRateTermStructure,
    MarketStatus,
    OrderBookSnapshot,
    Platform,
    RegulatoryEvent,
    RegulatoryRiskMatrix,
    UnifiedEventProbability,
)
from .quant_engine import build_unified_event, compute_bbo_midpoint, compute_conviction_score

# ──────────────────────────────────────────────────────────────
# Config loading (dotenv — not os.getenv in app code)
# ──────────────────────────────────────────────────────────────

_ENV_PATH: Path = Path(__file__).parent / ".env"
_config: dict[str, str | None] = dotenv_values(_ENV_PATH)


def _cfg(key: str, default: str = "") -> str:
    """Read a config value from the .env file."""
    return str(_config.get(key, default) or default)


# ──────────────────────────────────────────────────────────────
# Client singletons
# ──────────────────────────────────────────────────────────────

_poly_client: PolymarketClient = PolymarketClient(
    gamma_base_url=_cfg("POLYMARKET_GAMMA_BASE_URL", "https://gamma-api.polymarket.com"),
    clob_base_url=_cfg("POLYMARKET_CLOB_BASE_URL", "https://clob.polymarket.com"),
    timeout=float(_cfg("HTTP_TIMEOUT_SECONDS", "10.0")),
)

_kalshi_client: KalshiClient = KalshiClient(
    base_url=_cfg("KALSHI_BASE_URL", "https://api.elections.kalshi.com/trade-api/v2"),
    api_key=_cfg("KALSHI_API_KEY"),
    timeout=float(_cfg("HTTP_TIMEOUT_SECONDS", "10.0")),
)

# ──────────────────────────────────────────────────────────────
# FastMCP server
# ──────────────────────────────────────────────────────────────

mcp = FastMCP(
    "prediction-market",
)


def _serialize(obj: Any) -> str:
    """Serialize a Pydantic model or dict to JSON string via msgspec."""
    if hasattr(obj, "model_dump"):
        return msgspec.json.encode(obj.model_dump(mode="json")).decode("utf-8")
    return msgspec.json.encode(obj).decode("utf-8")


# ──────────────────────────────────────────────────────────────
# Tool: search_macro_events
# ──────────────────────────────────────────────────────────────

@mcp.tool()
async def search_macro_events(
    query: str,
    min_liquidity_usd: float = 25000.0,
) -> str:
    """
    Search both platforms for macro-event contracts.

    Section 5 Architecture: Macro Context — Capital-Weighted Probabilities.
    Searches Polymarket and Kalshi for contracts matching terms like
    "SEC", "Fed", "FOMC", or "ETF". Returns standardized
    UnifiedEventProbability objects filtered by minimum liquidity.

    Args:
        query: Search terms (e.g. "SEC ruling", "Fed rate cut").
        min_liquidity_usd: Minimum OI in USD to include a market.

    Returns:
        JSON array of UnifiedEventProbability objects.
    """
    poly_task = _search_polymarket(query, min_liquidity_usd)
    kalshi_task = _search_kalshi(query, min_liquidity_usd)
    poly_results, kalshi_results = await asyncio.gather(
        poly_task, kalshi_task, return_exceptions=True,
    )

    events: list[dict[str, Any]] = []
    if isinstance(poly_results, list):
        events.extend(poly_results)
    else:
        logger.error("Polymarket search failed | error={}", poly_results)
    if isinstance(kalshi_results, list):
        events.extend(kalshi_results)
    else:
        logger.error("Kalshi search failed | error={}", kalshi_results)

    events.sort(key=lambda e: e.get("capital_conviction_score", 0), reverse=True)
    return _serialize(events)


async def _search_polymarket(
    query: str,
    min_liquidity_usd: float,
) -> list[dict[str, Any]]:
    """Search Polymarket and build unified events in parallel."""
    markets: list[dict[str, Any]] = await _poly_client.search_markets(query)
    tasks = [_build_poly_event(m) for m in markets]
    events = await asyncio.gather(*tasks, return_exceptions=True)

    results: list[dict[str, Any]] = []
    min_oi = Decimal(str(min_liquidity_usd))

    for event in events:
        if isinstance(event, UnifiedEventProbability) and event.open_interest_usd >= min_oi:
            results.append(event.model_dump(mode="json"))
        elif isinstance(event, Exception):
            logger.warning("Polymarket build failed | error={}", event)

    return results


async def _build_poly_event(
    market: dict[str, Any],
) -> UnifiedEventProbability | None:
    """
    Build a unified event from a Polymarket market dict.

    Args:
        market: Raw Polymarket market data.

    Returns:
        UnifiedEventProbability or None on failure.
    """
    try:
        tokens: list[Any] = market.get("tokens", [])
        token_id: str = _extract_yes_token_id(tokens)
        if not token_id:
            return None

        book_raw: dict[str, Any] = await _poly_client.fetch_order_book(token_id)
        book: OrderBookSnapshot = _parse_poly_order_book(market, book_raw)
        return await build_unified_event(market, book, Platform.POLYMARKET)

    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("Polymarket event build failed | error={}", exc)
        return None


def _extract_yes_token_id(tokens: list[Any]) -> str:
    """Extract the YES token ID from Polymarket token list."""
    for token in tokens:
        if isinstance(token, dict) and token.get("outcome", "").lower() == "yes":
            return str(token.get("token_id", ""))
    if tokens and isinstance(tokens[0], dict):
        return str(tokens[0].get("token_id", ""))
    return ""


def _parse_poly_order_book(
    market: dict[str, Any],
    book_raw: dict[str, Any],
) -> OrderBookSnapshot:
    """Parse Polymarket CLOB response into an OrderBookSnapshot."""
    bids: list[list[float]] = _parse_levels(book_raw.get("bids", []))
    asks: list[list[float]] = _parse_levels(book_raw.get("asks", []))
    return OrderBookSnapshot(
        market_id=str(market.get("condition_id", "")),
        platform=Platform.POLYMARKET,
        bids=bids,
        asks=asks,
    )


def _parse_levels(raw_levels: list[Any]) -> list[list[float]]:
    """Parse raw order book levels to [[price, size], ...]."""
    result: list[list[float]] = []
    for level in raw_levels:
        if isinstance(level, dict):
            price: float = float(level.get("price", 0))
            size: float = float(level.get("size", 0))
            result.append([price, size])
        elif isinstance(level, (list, tuple)) and len(level) >= 2:
            result.append([float(level[0]), float(level[1])])
    return result


async def _search_kalshi(
    query: str,
    min_liquidity_usd: float,
) -> list[dict[str, Any]]:
    """Search Kalshi events/markets and build unified events in parallel."""
    if not _kalshi_client.is_available:
        return []

    events_raw: list[dict[str, Any]] = await _kalshi_client.search_events(query)
    market_tasks = []
    for event in events_raw:
        ticker = str(event.get("event_ticker", ""))
        market_tasks.append(_kalshi_client.fetch_markets_for_event(ticker))

    market_groups = await asyncio.gather(*market_tasks, return_exceptions=True)
    all_markets = []
    for group in market_groups:
        if isinstance(group, list):
            all_markets.extend(group)

    build_tasks = [_build_kalshi_event(m) for m in all_markets]
    unified_events = await asyncio.gather(*build_tasks, return_exceptions=True)

    results: list[dict[str, Any]] = []
    min_oi = Decimal(str(min_liquidity_usd))

    for event in unified_events:
        if isinstance(event, UnifiedEventProbability) and event.open_interest_usd >= min_oi:
            results.append(event.model_dump(mode="json"))

    return results


async def _build_kalshi_event(
    market: dict[str, Any],
) -> UnifiedEventProbability | None:
    """
    Build a unified event from a Kalshi market dict.

    Args:
        market: Raw Kalshi market data.

    Returns:
        UnifiedEventProbability or None on failure.
    """
    try:
        ticker: str = str(market.get("ticker", ""))
        book_raw: dict[str, Any] = await _kalshi_client.fetch_order_book(ticker)
        book: OrderBookSnapshot = _parse_kalshi_order_book(market, book_raw)
        return await build_unified_event(market, book, Platform.KALSHI)

    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("Kalshi event build failed | error={}", exc)
        return None


def _parse_kalshi_order_book(
    market: dict[str, Any],
    book_raw: dict[str, Any],
) -> OrderBookSnapshot:
    """Parse Kalshi order book — normalise cents to 0–1."""
    yes_raw: list[Any] = book_raw.get("orderbook", {}).get("yes", [])
    no_raw: list[Any] = book_raw.get("orderbook", {}).get("no", [])

    bids: list[list[float]] = _normalise_kalshi_levels(yes_raw)
    asks: list[list[float]] = _derive_asks_from_no(no_raw)

    return OrderBookSnapshot(
        market_id=str(market.get("ticker", "")),
        platform=Platform.KALSHI,
        bids=bids,
        asks=asks,
    )


def _normalise_kalshi_levels(levels: list[Any]) -> list[list[float]]:
    """Normalise Kalshi cents levels to 0–1."""
    result: list[list[float]] = []
    for level in levels:
        if isinstance(level, (list, tuple)) and len(level) >= 2:
            price: float = float(level[0]) / 100.0
            size: float = float(level[1])
            result.append([price, size])
    return result


def _derive_asks_from_no(no_levels: list[Any]) -> list[list[float]]:
    """Derive YES asks from NO bids (ask = 1 - no_bid)."""
    result: list[list[float]] = []
    for level in no_levels:
        if isinstance(level, (list, tuple)) and len(level) >= 2:
            ask_price: float = 1.0 - (float(level[0]) / 100.0)
            size: float = float(level[1])
            result.append([ask_price, size])
    return sorted(result, key=lambda x: x[0])


# ──────────────────────────────────────────────────────────────
# Tool: get_orderbook_probability
# ──────────────────────────────────────────────────────────────

@mcp.tool()
async def get_orderbook_probability(
    market_id: str,
    platform: str,
) -> str:
    """
    Fetch live order book and compute BBO midpoint probability.

    Section 5 Architecture: Macro Context — Capital-Weighted Probabilities.
    Returns the current probability, spread metrics, and
    capital_conviction_score for a single market.

    Args:
        market_id: Platform-specific market/contract identifier.
        platform: 'polymarket' or 'kalshi'.

    Returns:
        JSON UnifiedEventProbability.
    """
    plat: Platform = Platform(platform.lower())

    if plat == Platform.POLYMARKET:
        event = await _get_poly_probability(market_id)
    else:
        event = await _get_kalshi_probability(market_id)

    if event is None:
        return _serialize({"error": "market_not_found", "market_id": market_id})
    return _serialize(event.model_dump(mode="json"))


async def _get_poly_probability(
    market_id: str,
) -> UnifiedEventProbability | None:
    """Fetch Polymarket order book and compute probability."""
    try:
        market: dict[str, Any] = await _poly_client.fetch_market_by_id(market_id)
        if not market:
            return None
        tokens = market.get("tokens", [])
        token_id = _extract_yes_token_id(tokens)
        if not token_id:
            return None
        book_raw = await _poly_client.fetch_order_book(token_id)
        book = _parse_poly_order_book(market, book_raw)
        return await build_unified_event(market, book, Platform.POLYMARKET)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.error("Poly probability fetch failed | id={} | error={}", market_id, exc)
        return None


async def _get_kalshi_probability(
    market_id: str,
) -> UnifiedEventProbability | None:
    """Fetch Kalshi order book and compute probability."""
    try:
        if not _kalshi_client.is_available:
            return None
        book_raw = await _kalshi_client.fetch_order_book(market_id)
        if not book_raw:
            return None
        market: dict[str, Any] = {"ticker": market_id, "title": market_id}
        book = _parse_kalshi_order_book(market, book_raw)
        return await build_unified_event(market, book, Platform.KALSHI)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.error("Kalshi probability fetch failed | id={} | error={}", market_id, exc)
        return None


# ──────────────────────────────────────────────────────────────
# Tool: get_fed_rate_implied_path
# ──────────────────────────────────────────────────────────────

@mcp.tool()
async def get_fed_rate_implied_path() -> str:
    """
    Construct probability-weighted Fed rate term structure.

    Section 5 Architecture: Macro Context — Capital-Weighted Probabilities.
    Fetches all active FOMC/Fed rate markets across both platforms
    and builds a chronological term structure of cut/hold/hike
    probabilities for upcoming meetings.

    Returns:
        JSON FedRateTermStructure.
    """
    fed_events: list[UnifiedEventProbability] = await _fetch_fed_markets()
    meetings: list[FedRateMeeting] = _build_fed_meetings(fed_events)
    term_structure: FedRateTermStructure = _build_term_structure(meetings)
    return _serialize(term_structure.model_dump(mode="json"))


async def _fetch_fed_markets() -> list[UnifiedEventProbability]:
    """Fetch Fed-related markets from both platforms and return unified events."""
    queries: list[str] = ["fed rate", "fomc", "rate cut", "federal reserve"]
    poly_tasks = [_poly_client.search_markets(q, limit=20) for q in queries]
    kalshi_tasks = []
    if _kalshi_client.is_available:
        kalshi_tasks = [_kalshi_client.search_events(q, limit=20) for q in queries]

    all_raw = await asyncio.gather(*(poly_tasks + kalshi_tasks), return_exceptions=True)
    all_markets: list[tuple[dict[str, Any], Platform]] = []
    
    # Process Poly markets and Kalshi events (fetch markets for events)
    for i, res in enumerate(all_raw):
        if not isinstance(res, list): continue
        if i < len(poly_tasks):
            all_markets.extend([(m, Platform.POLYMARKET) for m in res])
        else:
            for ev in res:
                mkts = await _kalshi_client.fetch_markets_for_event(ev.get("event_ticker", ""))
                all_markets.extend([(m, Platform.KALSHI) for m in mkts])

    build_tasks = []
    for mkt, plat in all_markets:
        if plat == Platform.POLYMARKET: build_tasks.append(_build_poly_event(mkt))
        else: build_tasks.append(_build_kalshi_event(mkt))

    events = await asyncio.gather(*build_tasks, return_exceptions=True)
    return [e for e in events if isinstance(e, UnifiedEventProbability) and _is_fed_market(e.question.lower())]


def _build_fed_meetings(
    events: list[UnifiedEventProbability],
) -> list[FedRateMeeting]:
    """Parse unified events into FedRateMeeting list."""
    meetings: list[FedRateMeeting] = []
    for event in events:
        cut_prob, hold_prob, hike_prob = _parse_fed_probabilities(event)
        meetings.append(FedRateMeeting(
            meeting_date=event.end_date,
            cut_probability=cut_prob,
            hold_probability=hold_prob,
            hike_probability=hike_prob,
            capital_conviction_score=event.capital_conviction_score,
            source_markets=[event.market_id],
            status=event.status,
        ))
    return meetings


def _is_fed_market(question: str) -> bool:
    """Check if a question relates to Fed rate decisions using word boundaries."""
    pattern = r"\b(fed|fomc|rate|federal reserve)\b"
    return bool(re.search(pattern, question.lower()))


def _parse_fed_probabilities(
    event: UnifiedEventProbability,
) -> tuple[float, float, float]:
    """Extract cut/hold/hike probabilities from unified event."""
    prob: float = event.probability
    question: str = event.question.lower()
    
    if "cut" in question:
        return prob, 1.0 - prob, 0.0
    if "hike" in question:
        return 0.0, 1.0 - prob, prob
    return 0.0, prob, 0.0


def _build_term_structure(
    meetings: list[FedRateMeeting],
) -> FedRateTermStructure:
    """Build the term structure from meeting data."""
    meetings_sorted: list[FedRateMeeting] = sorted(
        meetings, key=lambda m: m.meeting_date
    )
    implied_cuts: float = sum(m.cut_probability for m in meetings_sorted)
    dominant: str = _determine_dominant_path(meetings_sorted)

    return FedRateTermStructure(
        meetings=meetings_sorted,
        implied_cuts_12m=round(implied_cuts, 2),
        dominant_path=dominant,
        status=MarketStatus.OK if meetings_sorted else MarketStatus.DEGRADED,
    )


def _determine_dominant_path(
    meetings: list[FedRateMeeting],
) -> str:
    """Determine the dominant rate path from meetings."""
    if not meetings:
        return "hold"
    avg_cut: float = sum(m.cut_probability for m in meetings) / len(meetings)
    avg_hike: float = sum(m.hike_probability for m in meetings) / len(meetings)
    if avg_cut > 0.5:
        return "easing"
    if avg_hike > 0.5:
        return "tightening"
    return "hold"


# ──────────────────────────────────────────────────────────────
# Tool: get_regulatory_risk_matrix
# ──────────────────────────────────────────────────────────────

@mcp.tool()
async def get_regulatory_risk_matrix() -> str:
    """
    Aggregate crypto-regulatory market probabilities into a risk matrix.

    Section 5 Architecture: Macro Context — Capital-Weighted Probabilities.
    Scans both platforms for SEC, CFTC, and crypto-regulation markets.
    Returns a RegulatoryRiskMatrix optimized for MacroCrossMarketAgent.

    Returns:
        JSON RegulatoryRiskMatrix.
    """
    reg_events: list[RegulatoryEvent] = await _fetch_regulatory_events()
    matrix: RegulatoryRiskMatrix = _build_risk_matrix(reg_events)
    return _serialize(matrix.model_dump(mode="json"))


async def _fetch_regulatory_events() -> list[RegulatoryEvent]:
    """Fetch regulatory markets from both platforms and return regulatory events."""
    queries: list[str] = ["SEC", "CFTC", "crypto regulation", "ETF approval"]
    
    # Search both platforms
    poly_search = [_poly_client.search_markets(q, limit=20) for q in queries]
    kalshi_search = []
    if _kalshi_client.is_available:
        kalshi_search = [_kalshi_client.search_events(q, limit=20) for q in queries]
    
    all_search = await asyncio.gather(*(poly_search + kalshi_search), return_exceptions=True)
    unified_tasks = []
    
    for i, res in enumerate(all_search):
        if not isinstance(res, list): continue
        if i < len(poly_search):
            unified_tasks.extend([_build_poly_event(m) for m in res])
        else:
            for ev in res:
                mkts = await _kalshi_client.fetch_markets_for_event(ev.get("event_ticker", ""))
                unified_tasks.extend([_build_kalshi_event(m) for m in mkts])

    unified_events = await asyncio.gather(*unified_tasks, return_exceptions=True)
    results: list[RegulatoryEvent] = []
    for e in unified_events:
        if isinstance(e, UnifiedEventProbability):
            reg_ev = _convert_to_regulatory_event(e)
            if reg_ev: results.append(reg_ev)
    return results


def _convert_to_regulatory_event(event: UnifiedEventProbability) -> RegulatoryEvent | None:
    """Convert a UnifiedEventProbability to a RegulatoryEvent if it matches agency criteria."""
    agency = _infer_agency(event.question)
    if agency == "other" and "crypto" not in event.question.lower():
        return None
        
    return RegulatoryEvent(
        market_id=event.market_id,
        platform=event.platform,
        question=event.question,
        agency=agency,
        probability=event.probability,
        capital_conviction_score=event.capital_conviction_score,
        impact_direction=_infer_impact(event.question),
        open_interest_usd=event.open_interest_usd,
        status=event.status,
    )


# _build_regulatory_event removed in favour of _convert_to_regulatory_event


def _infer_agency(question: str) -> str:
    """Infer regulatory agency from question text using regex."""
    q: str = question.lower()
    if re.search(r"\b(sec|securities)\b", q):
        return "SEC"
    if re.search(r"\b(cftc)\b", q):
        return "CFTC"
    if re.search(r"\b(fed|federal reserve)\b", q):
        return "Fed"
    if re.search(r"\b(congress|senate)\b", q):
        return "Congress"
    return "other"


def _infer_impact(question: str) -> str:
    """Infer crypto market impact direction."""
    q: str = question.lower()
    if any(kw in q for kw in ("approve", "approval", "etf")):
        return "bullish"
    if any(kw in q for kw in ("ban", "sue", "enforce", "crack")):
        return "bearish"
    return "neutral"


def _build_risk_matrix(
    events: list[RegulatoryEvent],
) -> RegulatoryRiskMatrix:
    """Assemble the final risk matrix."""
    sorted_events: list[RegulatoryEvent] = sorted(
        events, key=lambda e: e.capital_conviction_score, reverse=True
    )
    bias: float = _calculate_net_bias(sorted_events)
    high_count: int = sum(
        1 for e in sorted_events if e.capital_conviction_score > 0.7
    )
    return RegulatoryRiskMatrix(
        events=sorted_events,
        net_regulatory_bias=round(bias, 3),
        high_conviction_count=high_count,
        status=MarketStatus.OK if sorted_events else MarketStatus.DEGRADED,
    )


def _calculate_net_bias(events: list[RegulatoryEvent]) -> float:
    """Calculate net regulatory bias from -1 to +1."""
    if not events:
        return 0.0
    weighted_sum: float = 0.0
    total_weight: float = 0.0
    for event in events:
        direction: float = _direction_to_float(event.impact_direction)
        weight: float = event.capital_conviction_score
        weighted_sum += direction * weight * event.probability
        total_weight += weight
    if total_weight == 0.0:
        return 0.0
    return max(-1.0, min(1.0, weighted_sum / total_weight))


def _direction_to_float(direction: str) -> float:
    """Map direction string to numeric value."""
    if direction == "bullish":
        return 1.0
    if direction == "bearish":
        return -1.0
    return 0.0


# ──────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="stdio")
