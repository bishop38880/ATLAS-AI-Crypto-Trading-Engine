from __future__ import annotations

import asyncio
import os
import subprocess

import httpx
from fastapi import APIRouter, Request
from loguru import logger
from pydantic import BaseModel

from atlas.core.autonomous_rag_analysis import (
    AutonomousRAGAnalysisRunner,
    build_rag_query_engine,
)
from atlas.shared.config import PolarisSettings

router = APIRouter(prefix="/api/system", tags=["system"])


class ArmHydraResponse(BaseModel):
    status: str


class StartAutonomousEngineResponse(BaseModel):
    status: str


class AutonomousEngineStatusResponse(BaseModel):
    running: bool


class StartAllResponse(BaseModel):
    status: str
    hydra_status: str
    engine_status: str


class StartLmStudioRAGAnalysisResponse(BaseModel):
    status: str
    provider: str
    endpoint: str
    detail: str | None = None


async def _arm_hydra_process() -> ArmHydraResponse:
    logger.info("Arming Hydra backend and frontend...")
    try:
        # Resolve the absolute path to the ATLAS root directory
        atlas_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
        script_path = os.path.join(atlas_root, "scripts", "start_hydra.sh")

        # Start the script in the background
        subprocess.Popen(["bash", script_path], cwd=atlas_root)

        return ArmHydraResponse(status="success")
    except Exception as e:
        logger.error("Failed to arm hydra | err={}", str(e))
        return ArmHydraResponse(status="error")


@router.post("/arm-hydra", response_model=ArmHydraResponse)
async def arm_hydra() -> ArmHydraResponse:
    return await _arm_hydra_process()


def _lmstudio_bearer_token(settings: PolarisSettings) -> str:
    """Bearer LM Studio expects on /v1/* (matches ``MistralEmbeddingClient``)."""
    key = settings.embed_api_key.get_secret_value()
    if key:
        return key
    return "lm-studio"


async def _check_lmstudio_available(settings: PolarisSettings) -> bool:
    """Return True when LM Studio's OpenAI-compatible API is reachable."""
    endpoint = settings.lmstudio_base_url
    models_url = f"{endpoint.rstrip('/')}/models"
    headers = {"Authorization": f"Bearer {_lmstudio_bearer_token(settings)}"}
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(5.0, connect=2.0),
        ) as client:
            response = await client.get(models_url, headers=headers)
            return response.status_code == 200
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning(
            "lmstudio_health_check_failed | endpoint={} | err={}",
            endpoint,
            str(exc),
        )
        return False


async def _start_autonomous_runner(request: Request) -> StartAutonomousEngineResponse:
    """Start RAG-backed multi-asset analysis loop (idempotent)."""
    existing = getattr(request.app.state, "autonomous_runner", None)
    if existing is not None:
        return StartAutonomousEngineResponse(status="already_running")

    redis_client = request.app.state.redis
    settings = PolarisSettings()
    embedding_service = request.app.state.embedding_service
    rag_engine = await build_rag_query_engine(settings, embedding_service)
    rag_writer = getattr(request.app.state, "rag_writer", None)
    runner = AutonomousRAGAnalysisRunner(
        redis_client,
        settings,
        rag_engine,
        rag_writer,
    )
    try:
        await runner.start()
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("start_autonomous_engine failed")
        if rag_engine is not None:
            try:
                await rag_engine.aclose()
            except Exception:
                logger.exception("rag_engine_cleanup_after_start_failure")
        return StartAutonomousEngineResponse(status="error")

    request.app.state.autonomous_runner = runner
    logger.info("Autonomous RAG analysis loop started")
    return StartAutonomousEngineResponse(status="started")


@router.post("/start-autonomous-engine", response_model=StartAutonomousEngineResponse)
async def start_autonomous_engine(request: Request) -> StartAutonomousEngineResponse:
    """Start RAG-backed multi-asset analysis loop (idempotent)."""
    return await _start_autonomous_runner(request)


@router.post("/start-all", response_model=StartAllResponse)
async def start_all(request: Request) -> StartAllResponse:
    """Start the local ingestion and RAG-backed confluence analysis loops."""
    hydra_response = await _arm_hydra_process()
    engine_response = await _start_autonomous_runner(request)
    is_engine_ok = engine_response.status in {"started", "already_running"}
    if hydra_response.status == "success" and is_engine_ok:
        status = "started"
    elif is_engine_ok:
        status = "partial"
    else:
        status = "error"
    return StartAllResponse(
        status=status,
        hydra_status=hydra_response.status,
        engine_status=engine_response.status,
    )


@router.get("/autonomous-engine", response_model=AutonomousEngineStatusResponse)
async def autonomous_engine_status(request: Request) -> AutonomousEngineStatusResponse:
    """Return whether the AutonomousRAGAnalysisRunner loop is attached to this process."""
    return AutonomousEngineStatusResponse(
        running=getattr(request.app.state, "autonomous_runner", None) is not None,
    )


@router.post(
    "/start-lmstudio-rag-analysis",
    response_model=StartLmStudioRAGAnalysisResponse,
)
async def start_lmstudio_rag_analysis(
    request: Request,
) -> StartLmStudioRAGAnalysisResponse:
    """Start RAG analysis only when LM Studio is reachable."""
    settings = PolarisSettings()
    endpoint = settings.lmstudio_base_url
    is_available = await _check_lmstudio_available(settings)
    if not is_available:
        return StartLmStudioRAGAnalysisResponse(
            status="lmstudio_unavailable",
            provider="lmstudio",
            endpoint=endpoint,
            detail="LM Studio must be running with the local server enabled.",
        )

    start_response = await _start_autonomous_runner(request)
    return StartLmStudioRAGAnalysisResponse(
        status=start_response.status,
        provider="lmstudio",
        endpoint=endpoint,
    )
