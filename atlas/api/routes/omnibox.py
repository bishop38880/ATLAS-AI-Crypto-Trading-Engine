from __future__ import annotations

import msgspec
import httpx
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from atlas.api.omnibox_stream import stream_omnibox_answer
from atlas.rag.embedding_service import EmbeddingService
from atlas.shared.config import PolarisSettings

router = APIRouter(prefix="/api/omnibox", tags=["omnibox"])

_BaseConfig = ConfigDict(
    frozen=True,
    populate_by_name=True,
    alias_generator=to_camel,
)


class OmniBoxBudget(BaseModel):
    """Operator-facing budget meter — authoritative enforcement remains server-side."""

    model_config = _BaseConfig

    session_queries_used: int = Field(default=0, ge=0)
    session_query_limit: int = Field(default=50, ge=1)
    session_tokens_used: int = Field(default=0, ge=0)
    session_token_limit: int = Field(default=100_000, ge=1)
    estimated_cost_usd: str = Field(default="0.00")
    daily_cost_cap_usd: str = Field(default="50.00")
    queries_this_minute: int = Field(default=0, ge=0)
    queries_per_minute_limit: int = Field(default=12, ge=1)
    daily_cap_reached: bool = False
    cost_cap_reset_hours: int | None = Field(default=None, ge=0)
    operator_override_path: str | None = Field(default="/settings")


@router.get("/budget", response_model=OmniBoxBudget, response_model_by_alias=True)
async def get_budget() -> OmniBoxBudget:
    return OmniBoxBudget()


class OmniBoxQueryRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    query: str
    asset: str
    route_override: str | None = None


def _sse_line(payload: dict[str, object]) -> str:
    return "data: {}\n\n".format(msgspec.json.encode(payload).decode("utf-8"))


@router.post("/query")
async def post_query(
    http_request: Request,
    payload: OmniBoxQueryRequest,
) -> StreamingResponse:
    sse_headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    embed = getattr(http_request.app.state, "embedding_service", None)
    if not isinstance(embed, EmbeddingService):

        async def _missing_embed():
            yield _sse_line(
                {
                    "type": "token",
                    "data": "OmniBox cannot start: embedding service is not initialised on the API.",
                },
            )
            yield _sse_line(
                {
                    "type": "done",
                    "data": {
                        "latencyMs": 0,
                        "llmTier": "DEGRADED",
                        "liveDataSummary": None,
                    },
                },
            )

        return StreamingResponse(
            _missing_embed(),
            media_type="text/event-stream; charset=utf-8",
            headers=sse_headers,
        )

    http_client = getattr(http_request.app.state, "atlas_llm_http", None)
    if not isinstance(http_client, httpx.AsyncClient):

        async def _missing_http():
            yield _sse_line(
                {
                    "type": "token",
                    "data": "OmniBox cannot reach LM Studio / DeepSeek: HTTP client missing from app state.",
                },
            )
            yield _sse_line(
                {
                    "type": "done",
                    "data": {
                        "latencyMs": 0,
                        "llmTier": "DEGRADED",
                        "liveDataSummary": None,
                    },
                },
            )

        return StreamingResponse(
            _missing_http(),
            media_type="text/event-stream; charset=utf-8",
            headers=sse_headers,
        )

    settings = PolarisSettings()
    redis = http_request.app.state.redis

    return StreamingResponse(
        stream_omnibox_answer(
            settings=settings,
            embedding_service=embed,
            redis=redis,
            http_client=http_client,
            query=payload.query,
            asset=payload.asset,
            route_override=payload.route_override,
        ),
        media_type="text/event-stream; charset=utf-8",
        headers=sse_headers,
    )
