import asyncio
import time
from datetime import datetime, timezone
from typing import Any

import asyncpg
import redis.asyncio as redis
from loguru import logger
from pydantic import BaseModel

from atlas.core.startup_errors import StartupFailedError, StartupStepError
from atlas.core.monitoring_telemetry import publish_startup_sequence, safe_monitoring_write
from atlas.shared.config import PolarisSettings


def validate_core_urls(config: PolarisSettings) -> None:
    """Ensure Redis / Postgres URL schemes are usable."""
    if not config.redis_url.startswith("redis://"):
        raise StartupStepError("Invalid redis_url")
    if not config.postgres_url.startswith(("postgresql://", "postgres://")):
        raise StartupStepError("Invalid postgres_url")


def validate_embedding_credentials(config: PolarisSettings) -> None:
    """Mistral key is optional when LM Studio embedding provider is selected."""
    provider = (config.embed_provider or "").lower().strip()
    if provider == "lmstudio":
        return
    secret_holder = getattr(config, "embed_api_key", None)
    key_val = ""
    if secret_holder is not None:
        getter = getattr(secret_holder, "get_secret_value", None)
        if callable(getter):
            key_val = str(getter())
        else:
            key_val = str(secret_holder)
    if not key_val:
        raise StartupStepError(
            "Missing embedding credentials (set MISTRAL_API_KEY or embed_provider=lmstudio)",
        )


def validate_latency_budget(config: PolarisSettings) -> None:
    latency = int(getattr(config, "pipeline_latency_target_ms", 80))
    if latency <= 0:
        raise StartupStepError("ATLAS_PIPELINE_LATENCY_TARGET_MS must be > 0")


def _require_redis_server_major_ge_7(info: dict[str, object]) -> None:
    raw_ver = info.get("redis_version")
    if raw_ver is None or raw_ver == "":
        logger.debug("redis_version_missing | skip_major_check")
        return
    try:
        major = int(str(raw_ver).split(".", maxsplit=1)[0])
    except ValueError:
        logger.warning("redis_version_unparsed | raw={} | skip_major_check", raw_ver)
        return
    if major < 7:
        raise StartupStepError(f"Redis version < 7.0: {raw_ver}")


class StartupStepResult(BaseModel, frozen=True):
    """Result of one startup step."""
    step: int
    name: str
    success: bool
    duration_ms: float
    detail: str
    critical: bool


class StartupReport(BaseModel, frozen=True):
    """Full startup report — all 9 steps."""
    started_at: datetime
    completed_at: datetime | None
    steps: list[StartupStepResult]
    overall_success: bool
    degraded_mode: bool
    halt_reason: str | None


class PolarisStartup:
    """Executes the 9-step POLARIS startup sequence."""

    RETRY_INTERVAL_S: int = 30
    MAX_RETRIES: int = 10

    def __init__(
        self,
        config: PolarisSettings,
        redis_client: redis.Redis,
        asyncpg_pool: asyncpg.Pool,
        qdrant_client: Any,
        registry: Any,
        rag_pipeline: Any,
        metrics_server: Any,
        orchestrator: Any,
    ) -> None:
        self._config = config
        self._redis = redis_client
        self._pg_pool = asyncpg_pool
        self._qdrant = qdrant_client
        self._registry = registry
        self._rag_pipeline = rag_pipeline
        self._metrics_server = metrics_server
        self._orchestrator = orchestrator
        self._tasks: set[asyncio.Task] = set()

    async def run(self) -> StartupReport:
        """Execute all 9 startup steps in order."""
        started = datetime.now(timezone.utc)
        steps = []
        degraded = False

        step_funcs = [
            (1, "Config", self._step_1_config, True),
            (2, "Redis", self._step_2_redis, True),
            (3, "PostgreSQL", self._step_3_postgres, True),
            (4, "Qdrant", self._step_4_qdrant, True),
            (5, "Tier 1 Providers", self._step_5_providers, True),
            (6, "Agents", self._step_6_agents, False),
            (7, "RAG Pipeline", self._step_7_rag, False),
            (8, "Metrics Server", self._step_8_metrics, False),
            (9, "Analysis Cycle Loop", self._step_9_cycle, False),
        ]

        for step_num, name, func, critical in step_funcs:
            res = await self._run_step_with_retries(step_num, name, func, critical)
            steps.append(res)
            if not res.success:
                if res.critical:
                    logger.critical("Halted at step {}: {}", step_num, res.detail)
                    raise StartupFailedError(res.detail)
                degraded = True

        report = StartupReport(
            started_at=started, completed_at=datetime.now(timezone.utc),
            steps=steps, overall_success=True, degraded_mode=degraded, halt_reason=None,
        )
        await safe_monitoring_write(
            publish_startup_sequence(self._redis, report),
            action="publish_startup_sequence",
        )
        return report

    async def _run_step_with_retries(
        self, step_num: int, name: str, func: Any, critical: bool
    ) -> StartupStepResult:
        """Execute a single step with retry logic."""
        start_ms = time.perf_counter_ns() / 1_000_000.0
        for attempt in range(self.MAX_RETRIES + 1):
            try:
                res = await func()
                if res: return res
                dur = time.perf_counter_ns() / 1_000_000.0 - start_ms
                return StartupStepResult(step=step_num, name=name, success=True, duration_ms=dur, detail="OK", critical=critical)
            except Exception as e:
                if not critical:
                    dur = time.perf_counter_ns() / 1_000_000.0 - start_ms
                    return StartupStepResult(step=step_num, name=name, success=False, duration_ms=dur, detail=str(e), critical=critical)
                if attempt == self.MAX_RETRIES:
                    dur = time.perf_counter_ns() / 1_000_000.0 - start_ms
                    return StartupStepResult(step=step_num, name=name, success=False, duration_ms=dur, detail=str(e), critical=critical)
                logger.error("Step {} failed: {}. Retrying in {}s...", step_num, e, self.RETRY_INTERVAL_S)
                await asyncio.sleep(self.RETRY_INTERVAL_S)
        raise StartupFailedError("Unreachable")

    async def _step_1_config(self) -> StartupStepResult | None:
        """Load config from pydantic-settings. Validate environment variables."""
        validate_core_urls(self._config)
        validate_embedding_credentials(self._config)
        validate_latency_budget(self._config)

    async def _step_2_redis(self) -> StartupStepResult | None:
        """Connect to Redis using redis.asyncio."""
        await self._redis.set("polaris:startup:probe", "1", ex=10)
        val = await self._redis.get("polaris:startup:probe")
        if val not in (b"1", "1"):
            raise StartupStepError("Redis probe failed")
            
        info = await self._redis.info()
        _require_redis_server_major_ge_7(dict(info))

    async def _step_3_postgres(self) -> StartupStepResult | None:
        """Connect to PostgreSQL via asyncpg. Verify read/write."""
        async with self._pg_pool.acquire(timeout=5.0) as conn:
            await conn.execute("SELECT 1", timeout=5.0)
            try:
                await conn.execute("INSERT INTO signal_history (id, asset) VALUES ('probe', 'TEST') ON CONFLICT DO NOTHING", timeout=5.0)
                await conn.execute("DELETE FROM signal_history WHERE id = 'probe'", timeout=5.0)
            except Exception as e:
                logger.warning("Postgres write probe failed: {}", e)

    async def _step_4_qdrant(self) -> StartupStepResult | None:
        """Connect to Qdrant. Verify collection health."""
        try:
            cols = await self._qdrant.get_collections()
            names = [c.name for c in cols.collections]
            for req in ["signal_history_vectors", "pattern_memory_vectors"]:
                if req not in names:
                    raise StartupStepError(f"Missing collection {req}")
                info = await self._qdrant.get_collection(req)
                if getattr(info.config.params.vectors, "size", 1024) != 1024:
                    raise StartupStepError(f"Collection {req} vector size != 1024")
        except Exception as e:
            raise StartupStepError(str(e))

    async def _step_5_providers(self) -> StartupStepResult | None:
        """Health check Tier 1 providers."""
        from atlas.core.registry import initialize_registry
        initialize_registry(self._config, self._redis)
        tier1 = ["coinalyze", "pyth", "hydra", "fear_greed", "okx", "coinapi"]
        tier2 = ["nansen", "fred", "defillama", "dune", "hypertracker"]
        
        for p in tier1:
            provider = await self._registry.get_provider(p)
            if provider and not await provider.health_check():
                raise StartupStepError(f"Tier 1 failure: {p}")
                
        for p in tier2:
            provider = await self._registry.get_provider(p)
            if provider and not await provider.health_check():
                return StartupStepResult(step=5, name="Tier 1 Providers", success=False, duration_ms=0.0, detail="Tier 2 failed", critical=False)

    async def _step_6_agents(self) -> StartupStepResult | None:
        """Register all agents."""
        agents = self._orchestrator._agents if hasattr(self._orchestrator, "_agents") else []
        if len(agents) not in (8, 11) and len(agents) > 0:
            raise Exception(f"Unexpected agent count: {len(agents)}")

    async def _step_7_rag(self) -> StartupStepResult | None:
        """Initialise RAG pipeline. Warm caches."""
        await self._rag_pipeline.warm_up()

    async def _step_8_metrics(self) -> StartupStepResult | None:
        """Start Prometheus metrics server."""
        await self._metrics_server.start()

    async def _step_9_cycle(self) -> StartupStepResult | None:
        """Begin analysis cycle loop."""
        task = asyncio.create_task(self._orchestrator.run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)


def _perf_ms() -> float:
    return time.perf_counter_ns() / 1_000_000.0


async def run_fastapi_startup_checks(
    config: PolarisSettings,
    redis_client: redis.Redis,
    asyncpg_pool: asyncpg.Pool | None,
    *,
    redis_using_fallback: bool = False,
) -> StartupReport:
    """Run Polaris startup steps 1–3 for the HTTP API lifespan.

    Steps 4–9 remain the responsibility of standalone ``PolarisStartup`` (full engine).
    Publishes the same Redis monitoring payload shape as the nine-step sequence.
    """
    started = datetime.now(timezone.utc)
    steps: list[StartupStepResult] = []

    t_cfg = _perf_ms()
    try:
        validate_core_urls(config)
        validate_embedding_credentials(config)
        validate_latency_budget(config)
        steps.append(
            StartupStepResult(
                step=1,
                name="Config",
                success=True,
                duration_ms=_perf_ms() - t_cfg,
                detail="OK",
                critical=True,
            ),
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        steps.append(
            StartupStepResult(
                step=1,
                name="Config",
                success=False,
                duration_ms=_perf_ms() - t_cfg,
                detail=str(exc),
                critical=False,
            ),
        )

    t_redis = _perf_ms()
    if redis_using_fallback:
        steps.append(
            StartupStepResult(
                step=2,
                name="Redis",
                success=True,
                duration_ms=_perf_ms() - t_redis,
                detail="degraded_in_memory_fallback_live_unreachable",
                critical=False,
            ),
        )
    else:
        try:
            await asyncio.wait_for(
                redis_client.set("polaris:startup:probe", "1", ex=10),
                timeout=float(config.redis_startup_probe_timeout_s),
            )
            val = await asyncio.wait_for(
                redis_client.get("polaris:startup:probe"),
                timeout=float(config.redis_startup_probe_timeout_s),
            )
            if val not in (b"1", "1"):
                raise StartupStepError("Redis probe failed")
            info_raw = await asyncio.wait_for(
                redis_client.info(),
                timeout=float(config.redis_startup_probe_timeout_s),
            )
            _require_redis_server_major_ge_7(dict(info_raw))
            steps.append(
                StartupStepResult(
                    step=2,
                    name="Redis",
                    success=True,
                    duration_ms=_perf_ms() - t_redis,
                    detail="OK",
                    critical=True,
                ),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            steps.append(
                StartupStepResult(
                    step=2,
                    name="Redis",
                    success=False,
                    duration_ms=_perf_ms() - t_redis,
                    detail=str(exc),
                    critical=True,
                ),
            )

    t_pg = _perf_ms()
    if asyncpg_pool is None:
        steps.append(
            StartupStepResult(
                step=3,
                name="PostgreSQL",
                success=False,
                duration_ms=_perf_ms() - t_pg,
                detail="pool_unavailable_degraded",
                critical=False,
            ),
        )
    else:
        try:
            async with asyncpg_pool.acquire(timeout=5.0) as conn:
                await conn.execute("SELECT 1")
            steps.append(
                StartupStepResult(
                    step=3,
                    name="PostgreSQL",
                    success=True,
                    duration_ms=_perf_ms() - t_pg,
                    detail="OK",
                    critical=True,
                ),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            steps.append(
                StartupStepResult(
                    step=3,
                    name="PostgreSQL",
                    success=False,
                    duration_ms=_perf_ms() - t_pg,
                    detail=str(exc),
                    critical=False,
                ),
            )

    deferred_labels = [
        (4, "Qdrant"),
        (5, "Tier 1 Providers"),
        (6, "Agents"),
        (7, "RAG Pipeline"),
        (8, "Metrics Server"),
        (9, "Analysis Cycle Loop"),
    ]
    for step_num, label in deferred_labels:
        steps.append(
            StartupStepResult(
                step=step_num,
                name=label,
                success=True,
                duration_ms=0.0,
                detail="deferred_full_polaris_startup",
                critical=False,
            ),
        )

    degraded = any(not step.success for step in steps if step.step <= 3)
    if redis_using_fallback:
        degraded = True
    report = StartupReport(
        started_at=started,
        completed_at=datetime.now(timezone.utc),
        steps=steps,
        overall_success=True,
        degraded_mode=degraded,
        halt_reason=None,
    )
    await safe_monitoring_write(
        publish_startup_sequence(redis_client, report),
        action="publish_fastapi_startup_checks",
    )
    return report
