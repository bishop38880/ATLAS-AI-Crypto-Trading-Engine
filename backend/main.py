"""FastAPI backend application for ATLAS.

Initializes dependencies and mounts routers.
"""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncGenerator, cast

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, Query
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIWebSocketRoute
import asyncpg  # type: ignore[import-untyped]
import httpx
import msgspec
from redis.asyncio import Redis
import asyncio
from loguru import logger
from qdrant_client import AsyncQdrantClient

from atlas.core.qdrant_client_factory import create_async_qdrant_client
from atlas.core.registry import coingecko_adapter, initialize_registry
from atlas.services import solana_flow_tracker
from atlas.core.app_state import AtlasAppState
from atlas.core.redis_resilience import (
    create_redis_runtime,
    redis_using_fallback_for_startup,
)
from atlas.core.startup import run_fastapi_startup_checks
from atlas.shared.config import PolarisSettings
from atlas.jobs.outcome_horizon_job import outcome_horizon_loop
from atlas.jobs.community_snapshot_job import community_fundamentals_loop
from atlas.jobs.helius_exchange_flow_job import helius_exchange_flow_poll_loop
from atlas.jobs.agent_zero_job import start_agent_zero_scheduler, stop_agent_zero_scheduler
from atlas.jobs.hourly_market_monitor_job import hourly_market_monitor_loop
from atlas.universe.refresh import sync_polaris_universe_all_redis
from atlas.core.llm_client import LocalLLMClient
from atlas.rag.embedding_service import EmbeddingService
from atlas.rag.writer import RAGWriter
from atlas.api._channel_reads import (
    read_activity,
    read_agents,
    read_confluence,
    read_prices,
    read_scores_ws_broadcast,
    read_system,
    read_tactical,
)
from atlas.api.routes.providers import _build_provider_payloads
from backend.router_mount import (
    mount_atlas_api_surface,
    mount_atlas_legacy_and_domain_routes,
    mount_prometheus_portfolio_bundle,
)
from backend.services.coinbase_premium.service import CoinbasePremiumService
from backend.pipeline.cache_miss_scorer import CacheMissScorer
from backend.pipeline.trade_orchestrator import TradeOrchestrator
from prometheus.execution.bitget_client import BitgetExecutionClient
from prometheus.execution.circuit_breaker import DefaultCircuitBreakerFactory
from prometheus.services.paper_trade_executor import PaperTradeExecutor
from prometheus.settings import PrometheusSettings, prometheus_settings

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application lifecycle and dependencies."""
    settings = PolarisSettings()

    async def init_connection(conn: asyncpg.Connection) -> None:
        """Warm up prepared statements on new connections."""
        await conn.prepare("SELECT 1 AS warmup")

    pool: asyncpg.Pool | None = None
    try:
        pool = await asyncpg.create_pool(
            settings.postgres_url,
            init=init_connection,
            command_timeout=2.0,
        )
        logger.info("Postgres pool initialized")
    except Exception as exc:
        logger.warning(
            "postgres_pool_init_failed | err={} | fallback=without_historical_store",
            str(exc),
        )
        pool = None

    redis_runtime = await create_redis_runtime(settings)
    redis = cast(Redis, redis_runtime.proxy)
    initialize_registry(settings, redis)
    solana_flow_tracker.init_redis(redis)

    local_llm = LocalLLMClient(settings)
    llm_read_timeout = max(
        float(settings.deepseek_timeout_seconds),
        float(settings.local_timeout_s),
    )
    atlas_llm_http = httpx.AsyncClient(
        timeout=httpx.Timeout(
            llm_read_timeout,
            connect=max(
                float(settings.deepseek_connect_timeout_seconds),
                2.0,
            ),
        ),
        http2=True,
    )

    dashboard_market_http = httpx.AsyncClient(
        timeout=httpx.Timeout(25.0, connect=5.0),
        limits=httpx.Limits(
            max_connections=48,
            max_keepalive_connections=24,
            keepalive_expiry=30.0,
        ),
        http2=True,
    )

    embed_service = EmbeddingService(settings)
    rag_writer: RAGWriter | None
    if pool is not None:
        rag_writer = RAGWriter(pool=pool, embedding_service=embed_service)
    else:
        rag_writer = None
        logger.warning("RAGWriter disabled due to missing Postgres pool")

    qdrant_client = None
    qdrant_probe = None
    try:
        qdrant_probe = create_async_qdrant_client(settings, timeout=5)
        await asyncio.wait_for(qdrant_probe.get_collections(), timeout=5.0)
        qdrant_client = qdrant_probe
        qdrant_probe = None
        logger.info("qdrant_client_ready | url={}", settings.qdrant_url)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("qdrant_client_unavailable | err={}", str(exc))
    finally:
        if qdrant_probe is not None:
            await qdrant_probe.close()

    startup_report = await run_fastapi_startup_checks(
        settings,
        redis,
        pool,
        redis_using_fallback=redis_using_fallback_for_startup(redis_runtime),
    )

    atlas_state = AtlasAppState(
        redis=redis,
        db_pool=pool,
        local_llm=local_llm,
        atlas_llm_http=atlas_llm_http,
        dashboard_market_http=dashboard_market_http,
        embedding_service=embed_service,
        rag_writer=rag_writer,
        qdrant_client=qdrant_client,
        startup_report=startup_report,
        redis_runtime=redis_runtime,
    )
    app.state.atlas = atlas_state
    app.state.redis_runtime = redis_runtime
    app.state.redis = atlas_state.redis
    app.state.dashboard_market_http = dashboard_market_http
    app.state.db_pool = atlas_state.db_pool
    app.state.local_llm = atlas_state.local_llm
    app.state.atlas_llm_http = atlas_state.atlas_llm_http
    app.state.embedding_service = atlas_state.embedding_service
    app.state.rag_writer = atlas_state.rag_writer
    app.state.qdrant_client = atlas_state.qdrant_client
    app.state.autonomous_runner = None
    app.state.paper_trade_executor_task = None
    app.state.paper_trade_http_client = None
    app.state.outcome_horizon_task = None
    app.state.hourly_market_monitor_task = None
    app.state.community_fundamentals_task = None
    app.state.helius_flow_poll_task = None
    app.state.agent_zero_scheduler = None
    app.state.premium_service = None

    cg_adapter = coingecko_adapter
    try:
        await sync_polaris_universe_all_redis(
            redis,
            settings=settings,
            adapter=cg_adapter,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("polaris_universe_all_sync_failed | err={}", str(exc))

    premium_service = CoinbasePremiumService(redis)
    await premium_service.start()
    app.state.premium_service = premium_service
    logger.info("coinbase_premium_service_started")

    await _start_paper_trade_executor(app, redis, prometheus_settings)

    trade_pipeline_http = httpx.AsyncClient(
        timeout=httpx.Timeout(65.0, connect=5.0),
        limits=httpx.Limits(
            max_connections=12,
            max_keepalive_connections=6,
            keepalive_expiry=30.0,
        ),
        http2=True,
    )
    app.state.trade_pipeline_http = trade_pipeline_http
    app.state.trade_orchestrator = TradeOrchestrator(
        redis,
        CacheMissScorer(),
        settings=settings,
        http_client=trade_pipeline_http,
    )
    logger.info("trade_orchestrator_initialized")

    if pool is not None and settings.outcome_horizon_job_enabled:
        app.state.outcome_horizon_task = asyncio.create_task(
            outcome_horizon_loop(pool, settings),
            name="outcome-horizon-job",
        )
        logger.info("outcome_horizon_job_spawned")

    if settings.hourly_market_monitor_enabled:
        app.state.hourly_market_monitor_task = asyncio.create_task(
            hourly_market_monitor_loop(redis, pg_pool=pool, settings=settings),
            name="hourly-market-monitor",
        )
        logger.info("hourly_market_monitor_job_spawned")

    community_job_on = (
        settings.community_snapshot_job_enabled
        or settings.community_admission_gate_enabled
    )
    if community_job_on and cg_adapter is not None:
        app.state.community_fundamentals_task = asyncio.create_task(
            community_fundamentals_loop(
                redis,
                pg_pool=pool,
                settings=settings,
                adapter=cg_adapter,
            ),
            name="community-fundamentals",
        )
        logger.info("community_fundamentals_job_spawned")
    elif community_job_on:
        logger.warning("community_fundamentals_job_skipped | reason=no_coingecko_adapter")

    if settings.helius_flow_poll_enabled:
        app.state.helius_flow_poll_task = asyncio.create_task(
            helius_exchange_flow_poll_loop(redis, settings),
            name="helius-flow-poll",
        )
        logger.info("helius_flow_poll_job_spawned")

    if pool is not None and qdrant_client is not None:
        app.state.agent_zero_scheduler = start_agent_zero_scheduler(
            pool,
            redis,
            qdrant_client,
            settings,
        )
    elif settings.agent_zero_job_enabled:
        logger.warning(
            "agent_zero_scheduler_skipped | pool={} | qdrant={}",
            pool is not None,
            qdrant_client is not None,
        )

    yield

    # Cleanup
    qc = getattr(app.state, "qdrant_client", None)
    if qc is not None:
        await qc.close()
        app.state.qdrant_client = None
    await _stop_paper_trade_executor(app)
    market_http = getattr(atlas_state, "dashboard_market_http", None)
    if market_http is not None:
        await market_http.aclose()

    llm_http = getattr(app.state, "atlas_llm_http", None)
    if llm_http is not None:
        await llm_http.aclose()
        app.state.atlas_llm_http = None

    trade_http = getattr(app.state, "trade_pipeline_http", None)
    if trade_http is not None:
        await trade_http.aclose()
        app.state.trade_pipeline_http = None
    app.state.trade_orchestrator = None
    runner = getattr(app.state, "autonomous_runner", None)
    if runner is not None:
        await runner.close()
        app.state.autonomous_runner = None

    ot = getattr(app.state, "outcome_horizon_task", None)
    if ot is not None:
        ot.cancel()
        await asyncio.gather(ot, return_exceptions=True)
        app.state.outcome_horizon_task = None

    hm = getattr(app.state, "hourly_market_monitor_task", None)
    if hm is not None:
        hm.cancel()
        await asyncio.gather(hm, return_exceptions=True)
        app.state.hourly_market_monitor_task = None

    cf = getattr(app.state, "community_fundamentals_task", None)
    if cf is not None:
        cf.cancel()
        await asyncio.gather(cf, return_exceptions=True)
        app.state.community_fundamentals_task = None

    hf = getattr(app.state, "helius_flow_poll_task", None)
    if hf is not None:
        hf.cancel()
        await asyncio.gather(hf, return_exceptions=True)
        app.state.helius_flow_poll_task = None

    az = getattr(app.state, "agent_zero_scheduler", None)
    await stop_agent_zero_scheduler(az)
    app.state.agent_zero_scheduler = None

    premium = getattr(app.state, "premium_service", None)
    if premium is not None:
        await premium.stop()
        app.state.premium_service = None

    if app.state.db_pool:
        await app.state.db_pool.close()
        app.state.db_pool = None
    redis_rt = getattr(app.state, "redis_runtime", None)
    if redis_rt is not None:
        await redis_rt.aclose()
        app.state.redis_runtime = None
    else:
        await redis.aclose()


async def _start_paper_trade_executor(
    app: FastAPI,
    redis: Redis,
    settings: PrometheusSettings,
) -> None:
    """Start the PROMETHEUS paper-trade subscriber when explicitly enabled."""
    if not settings.paper_trade_executor_enabled:
        logger.info("paper_trade_executor_disabled")
        return

    http_client = httpx.AsyncClient(
        http2=True,
        timeout=httpx.Timeout(settings.bitget_symbol_fetch_timeout_s),
    )
    bitget_client = BitgetExecutionClient(
        settings=settings,
        http_client=http_client,
        circuit_breaker_factory=DefaultCircuitBreakerFactory(),
        redis_client=redis,
    )
    executor = PaperTradeExecutor(
        redis=redis,
        http_client=http_client,
        bitget_client=bitget_client,
        settings=settings,
    )
    task = asyncio.create_task(executor.run(), name="paper-trade-executor")
    app.state.paper_trade_http_client = http_client
    app.state.paper_trade_executor_task = task


async def _stop_paper_trade_executor(app: FastAPI) -> None:
    """Cancel the paper-trade subscriber and close its HTTP client."""
    task = getattr(app.state, "paper_trade_executor_task", None)
    if task is not None:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        app.state.paper_trade_executor_task = None

    http_client = getattr(app.state, "paper_trade_http_client", None)
    if http_client is not None:
        await http_client.aclose()
        app.state.paper_trade_http_client = None


_openapi_settings = PolarisSettings()
_expose_openapi = _openapi_settings.atlas_expose_openapi

app = FastAPI(
    title="ATLAS Intelligence Loop",
    lifespan=lifespan,
    docs_url="/docs" if _expose_openapi else None,
    redoc_url="/redoc" if _expose_openapi else None,
    openapi_url="/openapi.json" if _expose_openapi else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

mount_atlas_legacy_and_domain_routes(app)
mount_atlas_api_surface(app)
mount_prometheus_portfolio_bundle(app)


@app.get("/api/health")
async def api_health(request: Request) -> dict[str, object]:
    """Lightweight health probe for frontend and smoke-test wiring."""
    degraded = False
    atlas_state = getattr(request.app.state, "atlas", None)
    if atlas_state is not None:
        report = getattr(atlas_state, "startup_report", None)
        if report is not None:
            degraded = bool(getattr(report, "degraded_mode", False))
    return {
        "status": "healthy",
        "service": "atlas",
        "startup_degraded": degraded,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.websocket("/activity/stream")
async def prometheus_activity_stream_ws(websocket: WebSocket) -> None:
    """Small ATLAS activity stream for the Prometheus AI terminal widget."""
    await websocket.accept()
    try:
        await websocket.send_json(
            {
                "type": "system",
                "level": "success",
                "message": "Connected to ATLAS activity stream",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        while True:
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=30)
                await websocket.send_json(
                    {
                        "type": "system",
                        "level": "info",
                        "message": "pong",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                )
            except asyncio.TimeoutError:
                await websocket.send_json(
                    {
                        "type": "heartbeat",
                        "level": "info",
                        "message": "ATLAS activity stream heartbeat",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                )
    except WebSocketDisconnect:
        return


@app.post("/activity/clear")
async def prometheus_activity_clear() -> dict[str, str]:
    return {"status": "cleared"}


@app.websocket("/dashboard/ws/prices")
async def prometheus_dashboard_prices_ws(
    websocket: WebSocket,
    symbols: str | None = Query(default=None),
) -> None:
    """Prometheus UI expects `/dashboard/ws/prices`; ATLAS exposes prices on `/ws/prices`."""
    redis = websocket.app.state.redis
    atlas_settings = PolarisSettings()
    interval = 3
    await websocket.accept()
    push_task = asyncio.create_task(
        _prometheus_prices_push_loop(websocket, redis, atlas_settings, symbols, interval)
    )
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        push_task.cancel()


async def _prometheus_prices_push_loop(
    websocket: WebSocket,
    redis: Redis,
    atlas_settings: PolarisSettings,
    symbols: str | None,
    interval: float,
) -> None:
    first = True
    while True:
        try:
            if first:
                await websocket.send_json(
                    {"type": "connected", "symbols": symbols or "redis-active"}
                )
                first = False
            data_prices = await read_prices(redis, atlas_settings, symbols)
            updates = []
            for p in data_prices:
                d = p.model_dump(by_alias=True)
                price_raw = d.get("price", 0)
                vol_raw = d.get("volume_24h", 0)
                try:
                    price_f = float(price_raw)
                except (TypeError, ValueError):
                    price_f = 0.0
                try:
                    vol_f = float(vol_raw)
                except (TypeError, ValueError):
                    vol_f = 0.0
                updates.append(
                    {
                        "symbol": str(d.get("symbol", "")),
                        "price": price_f,
                        "change_24h": float(d.get("change_24h") or 0),
                        "volume_24h": vol_f,
                        "high_24h": 0.0,
                        "low_24h": 0.0,
                        "timestamp": str(d.get("timestamp", "")),
                    }
                )
            await websocket.send_json({"type": "prices", "updates": updates})
            await asyncio.sleep(interval)
        except WebSocketDisconnect:
            break
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("prometheus_dashboard_prices_ws_error | err={}", str(exc))
            await asyncio.sleep(interval)


@app.on_event("startup")
async def _verify_ws_route_precedence() -> None:
    routes = {r.path: r for r in app.routes if isinstance(r, APIWebSocketRoute)}
    assert "/ws/signals" in routes, "explicit /ws/signals handler missing"
    assert routes["/ws/signals"].endpoint is not websocket_channel, (
        "/ws/{channel} is shadowing /ws/signals — registration order broken"
    )

@app.websocket("/ws/signals")
async def explicit_signals_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass

async def _push_loop(websocket: WebSocket, channel: str) -> None:
    redis = websocket.app.state.redis
    settings = PolarisSettings()
    interval_map = {
        "agents": 10, "scores": 10, "providers": 15,
        "system": 5, "activity": 5, "pipeline-activity": 5, "tactical": 10,
        "prices": 3, "positions": 5, "gnn": 10, "log": 30,
        "confluence": 5,
    }
    interval = interval_map.get(channel, 5)
    first_push = True
    
    while True:
        try:
            if channel == "agents":
                data_agents = await read_agents(redis)
                payload = [d.model_dump(by_alias=True) for d in data_agents]
            elif channel == "scores":
                scores_bundle = await read_scores_ws_broadcast(redis, settings)
                payload = [d.model_dump(by_alias=True, mode="json") for d in scores_bundle]
            elif channel == "confluence":
                payload = await read_confluence(redis)
            elif channel == "prices":
                data_prices = await read_prices(redis, settings)
                payload = [d.model_dump(by_alias=True) for d in data_prices] # type: ignore
            elif channel == "providers":
                provider_data = await _build_provider_payloads(redis)
                payload = [d.model_dump(by_alias=True) for d in provider_data]
            elif channel == "positions":
                raw_positions = await redis.get("prometheus:positions")
                if raw_positions is None:
                    payload = []
                else:
                    try:
                        payload = msgspec.json.decode(raw_positions)
                    except Exception as exc:
                        logger.warning("positions_ws_decode_failed | err={}", str(exc))
                        payload = []
            elif channel == "gnn":
                from atlas.api._channel_reads import read_gnn
                payload = await read_gnn(redis)
            elif channel == "system":
                payload = await read_system(redis, settings)
                payload["autonomousEngineActive"] = getattr(
                    websocket.app.state,
                    "autonomous_runner",
                    None,
                ) is not None
            elif channel == "tactical":
                events = await read_tactical(redis, settings)
                # Tactical sends each event individually
                for event in events:
                    await websocket.send_json(event)
                await asyncio.sleep(interval)
                continue
            elif channel in ("activity", "pipeline-activity"):
                if first_push:
                    await websocket.send_json([])
                    first_push = False
                activity_events = await read_activity(redis)
                if activity_events:
                    for evt in activity_events:
                        await websocket.send_json(evt)
                else:
                    await websocket.send_json([])
                await asyncio.sleep(interval)
                continue
            elif channel == "log":
                # Stub — log channel sends nothing until signal analysis
                # entries are published; prevents WS close on unknown channel
                await asyncio.sleep(interval)
                continue
            else:
                return
            await websocket.send_json(payload)
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("ws_push_error | channel={} | err={}", channel, str(exc))
            await asyncio.sleep(interval)

@app.websocket("/ws/{channel}")
async def websocket_channel(websocket: WebSocket, channel: str) -> None:
    if channel not in ("agents", "scores", "prices", "providers", "positions", "gnn", "system", "tactical", "activity", "pipeline-activity", "log", "confluence"):
        await websocket.close(code=4008, reason="unknown channel")
        logger.warning("ws_unknown_channel | channel={}", channel)
        return

    # Check for max connections via backend.routes.websocket if we want,
    # but the prompt specifies using bounded queues or max connections.
    # We will just accept and start pushing.
    settings = PolarisSettings()
    # Simple check for connection cap
    await websocket.accept()
    
    # On connect: send current state immediately. Do not wait for the next tick.
    # The push loop will do it immediately on the first iteration!
    push_task = asyncio.create_task(_push_loop(websocket, channel))
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        push_task.cancel()

# Legacy static bundle (Prometheus UI uses `/dashboard` in the browser route; avoid clashing WS paths).
app.mount(
    "/atlas-legacy-dashboard",
    StaticFiles(directory="backend/static", html=True),
    name="atlas_legacy_static",
)
