import asyncio
import jwt
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from loguru import logger
from redis.asyncio import Redis

from prometheus.api._portfolio import _build_portfolio_snapshot
from prometheus.api.routes.portfolio import router as portfolio_router
from prometheus.settings import prometheus_settings as settings

app = FastAPI(
    docs_url="/docs" if settings.expose_openapi_docs else None,
    redoc_url="/redoc" if settings.expose_openapi_docs else None,
    openapi_url="/openapi.json" if settings.expose_openapi_docs else None,
)

app.include_router(portfolio_router, prefix="/api/portfolio")

async def _validate_jwt_or_close(
    websocket: WebSocket, token: str | None
) -> str | None:
    if token is None:
        await websocket.accept()
        await websocket.close(code=4401, reason="auth_required")
        logger.bind(reason="missing_token").info("portfolio_ws_rejected")
        return None
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
        )
    except jwt.PyJWTError as e:
        await websocket.accept()
        await websocket.close(code=4401, reason="auth_required")
        logger.bind(reason="invalid_token", error=str(e)).info("portfolio_ws_rejected")
        return None
    return payload["sub"]

async def _portfolio_push_loop(websocket: WebSocket, user_id: str) -> None:
    await websocket.accept()
    log = logger.bind(user_id=user_id, channel="portfolio")
    log.info("ws_connected")
    redis = Redis.from_url(settings.redis_url)  # type: ignore[type-arg]
    try:
        while True:
            snapshot = await _build_portfolio_snapshot(user_id, redis)
            await websocket.send_text(snapshot.model_dump_json(by_alias=True))
            await asyncio.sleep(settings.PORTFOLIO_PUSH_INTERVAL_S)
    except WebSocketDisconnect:
        log.info("ws_disconnected")
    finally:
        await redis.aclose()

@app.websocket("/dashboard/ws/portfolio")
async def portfolio_ws(
    websocket: WebSocket,
    token: str | None = Query(default=None),
    user_id: str | None = Query(default=None),
) -> None:
    if settings.ws_allow_query_user_id and user_id:
        await _portfolio_push_loop(websocket, user_id)
        return
    sub = await _validate_jwt_or_close(websocket, token)
    if sub is None:
        return
    await _portfolio_push_loop(websocket, sub)
