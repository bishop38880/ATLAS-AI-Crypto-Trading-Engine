import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from atlas.core.startup import PolarisStartup, StartupReport
from atlas.core.startup_errors import StartupFailedError
from atlas.shared.config import PolarisSettings

@pytest.fixture
def mock_settings():
    settings = PolarisSettings(
        redis_url="redis://localhost:6379",
        postgres_url="postgresql://user:pass@localhost",
        qdrant_url="http://localhost:6333",
    )
    # inject dummy keys to pass Step 1 config checks
    setattr(settings, "embed_api_key", MagicMock(get_secret_value=lambda: "secret"))
    setattr(settings, "deepseek_api_key", MagicMock(get_secret_value=lambda: "secret"))
    return settings

@pytest.fixture
def mock_all_services(mock_settings):
    return PolarisStartup(
        config=mock_settings,
        redis_client=_mock_redis(),
        asyncpg_pool=_mock_postgres(),
        qdrant_client=_mock_qdrant(),
        registry=_mock_registry(),
        rag_pipeline=AsyncMock(),
        metrics_server=AsyncMock(),
        orchestrator=_mock_orch(),
    )

def _mock_redis():
    mock = AsyncMock()
    mock.get.return_value = b"1"
    mock.info.return_value = {"redis_version": "7.2.0"}
    return mock

def _mock_postgres():
    pool = MagicMock()
    conn = AsyncMock()
    ctx = AsyncMock()
    ctx.__aenter__.return_value = conn
    pool.acquire.return_value = ctx
    return pool

def _mock_qdrant():
    mock = AsyncMock()
    cols = MagicMock()
    c1, c2 = MagicMock(), MagicMock()
    c1.name, c2.name = "signal_history_vectors", "pattern_memory_vectors"
    cols.collections = [c1, c2]
    mock.get_collections.return_value = cols
    info = MagicMock()
    info.status, info.config.params.vectors.size = 1, 1024
    mock.get_collection.return_value = info
    return mock

def _mock_registry():
    reg = AsyncMock()
    prov = AsyncMock()
    prov.health_check.return_value = True
    reg.get_provider.return_value = prov
    return reg

def _mock_orch():
    orch = AsyncMock()
    orch._agents = [1] * 8
    return orch

@pytest.mark.asyncio
async def test_full_startup_all_healthy(mock_all_services):
    startup = mock_all_services
    report = await startup.run()
    assert report.overall_success is True
    assert report.degraded_mode is False
    assert len(report.steps) == 9

@pytest.mark.asyncio
async def test_startup_redis_failure_halts(mock_all_services):
    startup = mock_all_services
    startup._redis.get.return_value = b"0" # simulate probe failure
    startup.MAX_RETRIES = 0 # speed up test
    startup.RETRY_INTERVAL_S = 0
    with pytest.raises(StartupFailedError):
        await startup.run()

@pytest.mark.asyncio
async def test_startup_tier2_provider_failure_degraded(mock_all_services):
    startup = mock_all_services
    async def mock_get_provider(p):
        prov = AsyncMock()
        prov.health_check.return_value = p not in ["nansen"] # tier 2 failure
        return prov
    startup._registry.get_provider = mock_get_provider
    report = await startup.run()
    assert report.overall_success is True
    assert report.degraded_mode is True

@pytest.mark.asyncio
async def test_startup_tier1_provider_failure_halts(mock_all_services):
    startup = mock_all_services
    async def mock_get_provider(p):
        prov = AsyncMock()
        prov.health_check.return_value = p not in ["fear_greed"]  # tier 1 failure
        return prov
    startup._registry.get_provider = mock_get_provider
    startup.MAX_RETRIES = 0
    startup.RETRY_INTERVAL_S = 0
    with pytest.raises(StartupFailedError):
        await startup.run()

@pytest.mark.asyncio
async def test_startup_postgres_connected_no_pgvector_check(mock_all_services):
    startup = mock_all_services
    # just testing step 3 runs successfully without pgvector error
    res = await startup._step_3_postgres()
    assert res is None # success returns implicitly or None

@pytest.mark.asyncio
async def test_startup_qdrant_collection_missing_halts(mock_all_services):
    startup = mock_all_services
    collections_mock = MagicMock()
    collections_mock.collections = [MagicMock()]
    collections_mock.collections[0].name = "other"
    startup._qdrant.get_collections.return_value = collections_mock
    startup.MAX_RETRIES = 0
    startup.RETRY_INTERVAL_S = 0
    with pytest.raises(StartupFailedError):
        await startup.run()

@pytest.mark.asyncio
async def test_startup_rag_warmup_no_faiss(mock_all_services):
    startup = mock_all_services
    res = await startup._step_7_rag()
    startup._rag_pipeline.warm_up.assert_called_once()

@pytest.mark.asyncio
async def test_startup_rag_failure_degraded(mock_all_services):
    startup = mock_all_services
    startup._rag_pipeline.warm_up.side_effect = Exception("RAG down")
    report = await startup.run()
    assert report.overall_success is True
    assert report.degraded_mode is True

@pytest.mark.asyncio
async def test_startup_report_contains_all_9_steps(mock_all_services):
    startup = mock_all_services
    report = await startup.run()
    assert len(report.steps) == 9

@pytest.mark.asyncio
async def test_startup_latency_under_30_seconds(mock_all_services):
    import time
    startup = mock_all_services
    start = time.time()
    await startup.run()
    dur = time.time() - start
    assert dur < 30.0
