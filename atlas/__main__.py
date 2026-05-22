"""Optional standalone POLARIS engine CLI — **not** the production HTTP server."""

from __future__ import annotations

import asyncio
import os
import sys

from loguru import logger

from atlas.core.startup import PolarisStartup
from atlas.shared.config import PolarisSettings


async def main() -> None:
    """Reserved for nine-step ``PolarisStartup`` when fully wired by operators."""
    if os.environ.get("POLARIS_ALLOW_STANDALONE_CLI", "").lower() not in ("1", "true", "yes"):
        logger.error(
            "Standalone POLARIS CLI disabled by default. "
            "Start the API with uvicorn backend.main:app (see README). "
            "To experiment with python -m atlas, export POLARIS_ALLOW_STANDALONE_CLI=1 "
            "and inject real Redis, Postgres, Qdrant, registry, RAG, metrics, orchestrator.",
        )
        sys.exit(2)

    logger.warning(
        "POLARIS_ALLOW_STANDALONE_CLI is set — PolarisStartup expects real injected backends.",
    )
    settings = PolarisSettings()
    startup = PolarisStartup(
        config=settings,
        redis_client=None,  # type: ignore[arg-type]
        asyncpg_pool=None,  # type: ignore[arg-type]
        qdrant_client=None,
        registry=None,
        rag_pipeline=None,
        metrics_server=None,
        orchestrator=None,
    )
    report = await startup.run()
    logger.info("POLARIS startup report | degraded={}", report.degraded_mode)


if __name__ == "__main__":
    asyncio.run(main())
