"""Compatibility routes for the Prometheus Next.js dashboard.

These endpoints expose ATLAS data using the older Prometheus UI contract.
They are intentionally thin adapters: when ATLAS does not own the underlying
feature yet, they return stable empty/default payloads instead of 404s so the
dashboard can render while deeper integrations are filled in.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import msgspec
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from redis.asyncio import Redis

from atlas.api._channel_reads import read_prices
from atlas.core.asset_universe import get_active_assets
from atlas.core.autonomous_rag_analysis import (
    AutonomousRAGAnalysisRunner,
    build_rag_query_engine,
)
from atlas.shared.config import PolarisSettings


router = APIRouter(prefix="/api", tags=["prometheus-compat"])

_DEFAULT_WEIGHTS = {
    "trend": 30,
    "momentum": 25,
    "volume": 20,
    "structure": 15,
    "on_chain": 20,
    "coinglass": 15,
    "sentiment": 15,
    "macro": 10,
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _asset_name(symbol: str) -> str:
    names = {
        "BTC": "Bitcoin",
        "BTCUSDT": "Bitcoin",
        "ETH": "Ethereum",
        "ETHUSDT": "Ethereum",
        "SOL": "Solana",
        "SOLUSDT": "Solana",
        "BNB": "BNB",
        "BNBUSDT": "BNB",
        "ADA": "Cardano",
        "ADAUSDT": "Cardano",
        "XRP": "XRP",
        "XRPUSDT": "XRP",
        "DOGE": "Dogecoin",
        "DOGEUSDT": "Dogecoin",
    }
    return names.get(symbol.upper(), symbol.replace("USDT", ""))


def _normalize_symbol(symbol: str) -> str:
    symbol = symbol.upper().strip()
    return symbol if symbol.endswith("USDT") else f"{symbol}USDT"


def _price_to_asset(price_payload: Any) -> dict[str, Any]:
    data = price_payload.model_dump(by_alias=True)
    symbol = _normalize_symbol(str(data.get("symbol", "")))
    price = _as_float(data.get("price"))
    change = _as_float(data.get("change_24h"))
    volume = _as_float(data.get("volume_24h"))
    return {
        "symbol": symbol,
        "base": symbol.removesuffix("USDT"),
        "quote": "USDT",
        "name": _asset_name(symbol),
        "sector": "Crypto",
        "price": price,
        "change24h": change,
        "volume24h": volume,
        "marketCap": 0,
        "sparkline": [price] * 24 if price else [],
        "signals": {
            "1h": {"trend": "bullish" if change >= 0 else "bearish", "strength": abs(change)},
            "4h": {"trend": "bullish" if change >= 0 else "bearish", "strength": abs(change)},
            "1d": {"trend": "bullish" if change >= 0 else "bearish", "strength": abs(change)},
        },
        "aiConfidence": {
            "technical": 0.0,
            "onchain": 0.0,
            "sentiment": 0.0,
            "overall": 0.0,
        },
        "spread": 0,
        "depthImbalance": 0,
        "lastUpdate": data.get("timestamp") or _utc_now(),
    }


def _fallback_assets() -> list[dict[str, Any]]:
    seeds = [
        ("BTCUSDT", 0.0),
        ("ETHUSDT", 0.0),
        ("SOLUSDT", 0.0),
    ]
    return [
        {
            "symbol": symbol,
            "base": symbol.removesuffix("USDT"),
            "quote": "USDT",
            "name": _asset_name(symbol),
            "sector": "Crypto",
            "price": price,
            "change24h": 0,
            "volume24h": 0,
            "marketCap": 0,
            "sparkline": [],
            "signals": {
                "1h": {"trend": "neutral", "strength": 0},
                "4h": {"trend": "neutral", "strength": 0},
                "1d": {"trend": "neutral", "strength": 0},
            },
            "aiConfidence": {
                "technical": 0.0,
                "onchain": 0.0,
                "sentiment": 0.0,
                "overall": 0.0,
            },
            "spread": 0,
            "depthImbalance": 0,
            "lastUpdate": _utc_now(),
        }
        for symbol, price in seeds
    ]


async def _assets_from_atlas(redis: Redis, settings: PolarisSettings) -> list[dict[str, Any]]:
    prices = await read_prices(redis, settings)
    assets = [_price_to_asset(price) for price in prices]
    return assets or _fallback_assets()


def _symbol_to_frontend(symbol: str) -> str:
    return symbol.replace("/", "").upper()


def _signal_to_trader_result(signal: dict[str, Any]) -> dict[str, Any]:
    category = signal.get("category_scores") or {}
    symbol = _symbol_to_frontend(str(signal.get("asset") or "BTCUSDT"))
    score = int(signal.get("raw_confluence_score") or signal.get("score") or 0)
    # The frontend uses a 220-point scale, while SignalOutput.score is 0-100.
    if score <= 100:
        score = round(score * 2.2)
    confidence = signal.get("confidence")
    confidence_float = _as_float(confidence, 0.0)
    return {
        "type": "symbol_analyzed",
        "symbol": symbol,
        "score": score,
        "confidence": confidence_float,
        "decision": signal.get("decision") or "No Position",
        "summary": signal.get("reasoning_summary") or "",
        "timestamp": signal.get("timestamp") or _utc_now(),
        "breakdown": {
            "trend": category.get("technical", 0),
            "momentum": category.get("regime", 0),
            "volume": category.get("funding", 0),
            "structure": category.get("correlation", 0),
            "onChain": category.get("onchain", 0),
            "coinglass": category.get("derivatives", 0),
            "sentiment": category.get("sentiment", 0),
            "macro": category.get("news_macro", 0),
        },
    }


async def _latest_signal_payloads(redis: Redis) -> dict[str, dict[str, Any]]:
    payloads: dict[str, dict[str, Any]] = {}
    for asset in get_active_assets():
        key = f"polaris:signals:{asset.symbol}"
        try:
            raw = await redis.get(key)
            if not raw:
                continue
            data = msgspec.json.decode(raw)
            result = _signal_to_trader_result(data)
            payloads[result["symbol"]] = result
        except Exception:
            continue
    return payloads


async def _ensure_autonomous_runner(request: Request, interval_s: int) -> str:
    existing = getattr(request.app.state, "autonomous_runner", None)
    if existing is not None:
        return "already_running"

    settings = PolarisSettings()
    rag_engine = await build_rag_query_engine(settings, request.app.state.embedding_service)
    runner = AutonomousRAGAnalysisRunner(
        request.app.state.redis,
        settings,
        rag_engine,
        getattr(request.app.state, "rag_writer", None),
        interval_s=interval_s,
    )
    try:
        await runner.start()
    except Exception:
        if rag_engine is not None:
            await rag_engine.aclose()
        raise

    request.app.state.autonomous_runner = runner
    return "started"


def _trader_state(request: Request) -> dict[str, Any]:
    state = getattr(request.app.state, "autonomous_trader_state", None)
    if state is None:
        settings = PolarisSettings()
        state = {
            "state": "idle",
            "symbols": [_symbol_to_frontend(asset.symbol) for asset in get_active_assets()[:5]],
            "scan_interval": settings.confluence_cycle_interval_seconds,
            "auto_execute": False,
            "paper_trading": True,
            "weights": dict(_DEFAULT_WEIGHTS),
            "last_results": {},
            "updated_at": _utc_now(),
        }
        request.app.state.autonomous_trader_state = state
    return state


@router.get("/health/detailed")
async def get_prometheus_health(request: Request) -> dict[str, Any]:
    redis_ok = False
    try:
        await request.app.state.redis.ping()
        redis_ok = True
    except Exception:
        redis_ok = False

    db_pool = getattr(request.app.state, "db_pool", None)
    return {
        "status": "healthy" if redis_ok else "degraded",
        "service": "atlas-prometheus-compat",
        "timestamp": _utc_now(),
        "dependencies": {
            "redis": "healthy" if redis_ok else "unavailable",
            "postgres": "healthy" if db_pool is not None else "unavailable",
            "atlas": "healthy",
        },
    }


@router.get("/market/assets")
async def get_market_assets(request: Request) -> list[dict[str, Any]]:
    return await _assets_from_atlas(request.app.state.redis, PolarisSettings())


@router.get("/market/assets/{symbol}")
async def get_market_asset(symbol: str, request: Request) -> dict[str, Any]:
    assets = await _assets_from_atlas(request.app.state.redis, PolarisSettings())
    normalized = _normalize_symbol(symbol)
    for asset in assets:
        if asset["symbol"].upper() == normalized:
            return asset
    return {
        **_fallback_assets()[0],
        "symbol": normalized,
        "base": normalized.removesuffix("USDT"),
        "name": _asset_name(normalized),
    }


@router.get("/market/candles/{symbol}")
async def get_market_candles(
    symbol: str,
    request: Request,
    interval: str = "1h",
    limit: int = 100,
) -> dict[str, Any]:
    assets = await _assets_from_atlas(request.app.state.redis, PolarisSettings())
    normalized = _normalize_symbol(symbol)
    base_price = 0.0
    for asset in assets:
        if asset["symbol"].upper() == normalized:
            base_price = _as_float(asset.get("price"))
            break
    if base_price <= 0:
        base_price = 100.0

    interval_seconds = {
        "1m": 60,
        "5m": 300,
        "15m": 900,
        "1h": 3600,
        "4h": 14400,
        "1d": 86400,
    }.get(interval, 3600)
    limit = max(1, min(limit, 500))
    start = datetime.now(timezone.utc) - timedelta(seconds=interval_seconds * limit)
    candles = []
    for idx in range(limit):
        ts = start + timedelta(seconds=interval_seconds * idx)
        # Deterministic low-amplitude placeholder until ATLAS stores OHLC history.
        drift = ((idx % 11) - 5) / 1000
        close = base_price * (1 + drift)
        open_ = base_price * (1 + (((idx - 1) % 11) - 5) / 1000)
        high = max(open_, close) * 1.001
        low = min(open_, close) * 0.999
        candles.append(
            {
                "timestamp": int(ts.timestamp() * 1000),
                "open": round(open_, 8),
                "high": round(high, 8),
                "low": round(low, 8),
                "close": round(close, 8),
                "volume": 0,
                "quote_volume": 0,
            }
        )

    return {"success": True, "symbol": normalized, "interval": interval, "data": candles}


@router.get("/rag-trader/rag-monitor")
async def get_rag_monitor(asset: str = "BTC") -> dict[str, Any]:
    asset = asset.upper()
    return {
        "asset": asset,
        "status": "available",
        "timestamp": _utc_now(),
        "summary": (
            f"ATLAS compatibility mode is active for {asset}. "
            "Live RAG output will appear here once ATLAS publishes analysis snapshots."
        ),
        "markdown": (
            f"## {asset} Intelligence\n\n"
            "- ATLAS backend is connected.\n"
            "- Prometheus UI adapter is serving compatibility data.\n"
            "- Historical RAG analysis is not wired yet."
        ),
        "confidence": 0,
        "signals": [],
        "sources": [],
    }


@router.get("/autonomous-trader/status")
async def get_autonomous_trader_status(request: Request) -> dict[str, Any]:
    state = _trader_state(request)
    results = await _latest_signal_payloads(request.app.state.redis)
    if results:
        if getattr(request.app.state, "autonomous_runner", None) is not None:
            state["state"] = "analyzing"
        state["last_results"] = {
            symbol: {
                "score": result["score"],
                "confidence": result["decision"],
                "breakdown": result["breakdown"],
                "timestamp": result["timestamp"],
            }
            for symbol, result in results.items()
        }
    elif state["state"] != "idle":
        state["state"] = "waiting"
    return {
        **state,
        "runner_active": getattr(request.app.state, "autonomous_runner", None) is not None,
        "rag_writer_enabled": getattr(request.app.state, "rag_writer", None) is not None,
    }


@router.post("/autonomous-trader/start")
async def start_autonomous_trader(request: Request) -> dict[str, Any]:
    body = await request.json()
    state = _trader_state(request)
    settings = PolarisSettings()
    scan_interval = int(
        body.get("scan_interval")
        or state.get("scan_interval")
        or settings.confluence_cycle_interval_seconds
    )
    scan_interval = max(15, min(scan_interval, 3600))
    status = await _ensure_autonomous_runner(request, scan_interval)
    state.update(
        {
            "state": "starting" if status == "started" else "waiting",
            "symbols": body.get("symbols") or state["symbols"],
            "scan_interval": scan_interval,
            "auto_execute": bool(body.get("auto_execute", False)),
            "paper_trading": bool(body.get("paper_trading", True)),
            "updated_at": _utc_now(),
        }
    )
    return {"status": status, **state}


@router.post("/autonomous-trader/stop")
async def stop_autonomous_trader(request: Request) -> dict[str, Any]:
    runner = getattr(request.app.state, "autonomous_runner", None)
    if runner is not None:
        await runner.close()
        request.app.state.autonomous_runner = None
    state = _trader_state(request)
    state.update({"state": "idle", "updated_at": _utc_now()})
    return {"status": "stopped", **state}


@router.put("/autonomous-trader/weights")
async def update_autonomous_trader_weights(request: Request) -> dict[str, Any]:
    body = await request.json()
    state = _trader_state(request)
    weights = {**_DEFAULT_WEIGHTS, **{k: int(v) for k, v in body.items() if k in _DEFAULT_WEIGHTS}}
    state.update({"weights": weights, "updated_at": _utc_now()})
    return {"status": "updated", "weights": weights}


@router.post("/autonomous-trader/weights/reset")
async def reset_autonomous_trader_weights(request: Request) -> dict[str, Any]:
    state = _trader_state(request)
    state.update({"weights": dict(_DEFAULT_WEIGHTS), "updated_at": _utc_now()})
    return {"status": "reset", "weights": state["weights"]}


@router.post("/autonomous-trader/rotation/rescan")
async def rescan_autonomous_trader_rotation(request: Request) -> dict[str, Any]:
    assets = get_active_assets()
    picks = [
        {
            "symbol": _symbol_to_frontend(asset.symbol),
            "sector": asset.group,
            "score": max(50, 100 - idx * 4),
        }
        for idx, asset in enumerate(assets[:5])
    ]
    state = _trader_state(request)
    state.update({"symbols": [pick["symbol"] for pick in picks], "updated_at": _utc_now()})
    return {"status": "complete", "data": {"rotation_picks": picks}}


@router.get("/autonomous-trader/kraken/positions")
async def get_autonomous_trader_kraken_positions() -> dict[str, Any]:
    return {"positions": []}


@router.get("/autonomous-trader/kraken/account")
async def get_autonomous_trader_kraken_account() -> dict[str, Any]:
    return {
        "exchange": "kraken",
        "connected": False,
        "testnet": True,
        "balance": "0",
        "error": "Kraken execution is not connected; RAG analysis is running in paper mode.",
    }


@router.websocket("/autonomous-trader/ws")
async def autonomous_trader_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        await websocket.send_json(
            {
                "type": "connected",
                "state": "streaming",
                "timestamp": _utc_now(),
            }
        )
        while True:
            results = await _latest_signal_payloads(websocket.app.state.redis)
            if results:
                for result in results.values():
                    await websocket.send_json(result)
                    if result["score"] >= 110:
                        await websocket.send_json(
                            {
                                "type": "opportunity_detected",
                                "symbol": result["symbol"],
                                "score": result["score"],
                                "timestamp": _utc_now(),
                            }
                        )
                await websocket.send_json(
                    {"type": "scan_complete", "count": len(results), "timestamp": _utc_now()}
                )
            else:
                for asset in get_active_assets()[:5]:
                    symbol = _symbol_to_frontend(asset.symbol)
                    await websocket.send_json(
                        {
                            "type": "symbol_analyzed",
                            "symbol": symbol,
                            "score": 0,
                            "confidence": 0,
                            "decision": "No Position",
                            "timestamp": _utc_now(),
                            "breakdown": {
                                "trend": 0,
                                "momentum": 0,
                                "volume": 0,
                                "structure": 0,
                                "onChain": 0,
                                "coinglass": 0,
                                "sentiment": 0,
                                "macro": 0,
                            },
                        }
                    )
            await asyncio.sleep(5)
    except WebSocketDisconnect:
        return


@router.get("/positions")
async def get_dashboard_position_slots() -> list[dict[str, Any]]:
    """Six-slot portfolio ladder for the POLARIS live dashboard (display-only).

    The UI always renders six cells; PROMETHEUS/PaperTrade backends can populate
    these fields later without changing route shape.
    """
    slots: list[dict[str, Any]] = []
    for slot_index in range(1, 7):
        slots.append(
            {
                "slot_index": slot_index,
                "asset": None,
                "direction": None,
                "unrealized_pnl_percent": None,
            }
        )
    return slots


@router.get("/portfolio/stats")
async def get_portfolio_stats() -> dict[str, Any]:
    return {
        "totalValue": 0,
        "dailyPnl": 0,
        "dailyPnlPercent": 0,
        "openPositions": 0,
        "openOrders": 0,
        "timestamp": _utc_now(),
    }


@router.get("/portfolio/positions")
async def get_portfolio_positions() -> list[dict[str, Any]]:
    return []


@router.get("/portfolio/orders")
async def get_portfolio_orders() -> list[dict[str, Any]]:
    return []


@router.get("/portfolio/orders/history")
async def get_portfolio_order_history() -> list[dict[str, Any]]:
    return []


@router.get("/portfolio/trades")
async def get_portfolio_trades() -> list[dict[str, Any]]:
    return []


@router.get("/exchanges/status")
async def get_exchange_status() -> dict[str, Any]:
    return {"connected": False, "exchanges": [], "timestamp": _utc_now()}


@router.get("/risk/status")
async def get_risk_status() -> dict[str, Any]:
    return {"status": "unknown", "limits": {}, "breaches": [], "timestamp": _utc_now()}


@router.get("/reconciliation/status")
async def get_reconciliation_status() -> dict[str, Any]:
    return {"status": "idle", "discrepancies": 0, "timestamp": _utc_now()}


@router.get("/emergency/status")
async def get_emergency_status() -> dict[str, Any]:
    return {"enabled": False, "triggered": False, "timestamp": _utc_now()}


@router.get("/dlq/stats")
async def get_dlq_stats() -> dict[str, Any]:
    return {"failed": 0, "pendingRetry": 0, "timestamp": _utc_now()}


@router.get("/dlq/failed-orders")
async def get_failed_orders() -> list[dict[str, Any]]:
    return []


@router.get("/audit/events")
async def get_audit_events() -> list[dict[str, Any]]:
    return []


@router.get("/audit/event-types")
async def get_audit_event_types() -> list[str]:
    return []


@router.get("/orders/execution-stats")
async def get_order_execution_stats() -> dict[str, Any]:
    return {"total": 0, "filled": 0, "rejected": 0, "timestamp": _utc_now()}


@router.get("/algos/active-executions")
async def get_active_algo_executions() -> list[dict[str, Any]]:
    return []
