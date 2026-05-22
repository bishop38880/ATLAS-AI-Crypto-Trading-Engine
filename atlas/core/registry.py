"""Registry of active data providers and their HTTP pools.

Centralizes connection pooling so that instances are reused
across the ATLAS stack. HYDRA cascade data uses Redis Streams via
``HydraStreamListener``, not an HTTP provider pool registered here.
"""

import asyncio
from typing import Any
import redis.asyncio as redis_async

from atlas.core.circuit_breaker import get_circuit_breaker
from atlas.core.embedding_client import MistralEmbeddingClient
from atlas.core.llm_client import DeepSeekClient, LocalLLMClient
from atlas.core.provider_health import ProviderHealthTracker
from atlas.shared.config import PolarisSettings
import httpx
from atlas.shared.http_pool import ProviderHttpPool
from atlas.providers.nansen.provider import NansenProvider
from atlas.providers.pyth.connector import PythHermesConnector
from atlas.providers.dune.connector import DuneMCPProvider
from atlas.providers.fred.connector import FREDProvider
from atlas.providers.hypertracker.provider import HypertrackerProvider
from atlas.providers.alternative_me.connector import AlternativeMeConnector
from atlas.providers.coingecko.adapter import CoinGeckoAdapter
from atlas.providers.helius.provider import HeliusProvider
from atlas.providers.deribit.connector import DeribitOptionsProvider

# Module-level — None until initialize_registry() called
_config: PolarisSettings | None = None
deepseek_chat_client: DeepSeekClient | None = None
deepseek_reasoner_client: DeepSeekClient | None = None
local_llm_client: LocalLLMClient | None = None
mistral_embedding_client: MistralEmbeddingClient | None = None
coinalyze_pool: ProviderHttpPool | None = None
pyth_pool: ProviderHttpPool | None = None
defillama_pool: ProviderHttpPool | None = None
coinapi_pool: ProviderHttpPool | None = None
nansen_provider: NansenProvider | None = None
hypertracker_pool: ProviderHttpPool | None = None
hypertracker_provider: HypertrackerProvider | None = None
dune_provider: DuneMCPProvider | None = None
fred_provider: FREDProvider | None = None
_fred_http: httpx.AsyncClient | None = None
alternative_me_provider: AlternativeMeConnector | None = None
_alternative_me_http: httpx.AsyncClient | None = None
coingecko_adapter: CoinGeckoAdapter | None = None
helius_provider: HeliusProvider | None = None
_helius_http: httpx.AsyncClient | None = None
deribit_options_provider: DeribitOptionsProvider | None = None
_deribit_options_http: httpx.AsyncClient | None = None
pyth_connector: PythHermesConnector | None = None
_pyth_http: httpx.AsyncClient | None = None
health_tracker: ProviderHealthTracker | None = None
_hydra_ping_redis: redis_async.Redis | None = None  # type: ignore[type-arg]


class _HydraRedisPingProbe:
    """Redis PING against HYDRA logical store — ``health_check`` for REST probes."""

    def __init__(self, client: redis_async.Redis) -> None:  # type: ignore[type-arg]
        self._redis = client

    async def health_check(self) -> bool:
        try:
            return bool(await self._redis.ping())
        except Exception:
            return False


hydra_redis_ping_probe: _HydraRedisPingProbe | None = None


def initialize_registry(
    config: PolarisSettings,
    redis_client: redis_async.Redis,  # type: ignore[type-arg]
) -> None:
    """Called once from PolarisStartup._step_5_providers(). No-op if already initialized."""
    global _config, deepseek_chat_client, deepseek_reasoner_client, local_llm_client
    global mistral_embedding_client, coinalyze_pool, pyth_pool, defillama_pool
    global coinapi_pool, nansen_provider, pyth_connector, _pyth_http, health_tracker
    global hypertracker_pool, hypertracker_provider
    global dune_provider, fred_provider, _fred_http
    global alternative_me_provider, _alternative_me_http
    global coingecko_adapter, helius_provider, _helius_http
    global deribit_options_provider, _deribit_options_http
    global _hydra_ping_redis, hydra_redis_ping_probe

    if _config is not None:
        return

    _config = config
    deepseek_chat_client = DeepSeekClient(
        config,
        is_reasoner=False,
        redis_client=redis_client,
    )
    deepseek_reasoner_client = DeepSeekClient(
        config,
        is_reasoner=True,
        redis_client=redis_client,
    )
    local_llm_client = LocalLLMClient(config)
    mistral_embedding_client = MistralEmbeddingClient(config)
    coinalyze_pool = ProviderHttpPool(provider_name="coinalyze", base_url="https://api.coinalyze.net", redis_client=redis_client)
    pyth_pool = ProviderHttpPool(provider_name="pyth", base_url="https://hermes.pyth.network", redis_client=redis_client)
    defillama_pool = ProviderHttpPool(provider_name="defillama", base_url="https://yields.llama.fi", redis_client=redis_client)
    coinapi_pool = ProviderHttpPool(provider_name="coinapi", base_url="https://rest.coinapi.io", redis_client=redis_client)
    nansen_provider = NansenProvider(
        redis_client=redis_client,
        settings=config,
    )
    hypertracker_pool = ProviderHttpPool(
        provider_name="hypertracker",
        base_url="https://api.hyperliquid.xyz",
        redis_client=redis_client,
    )
    hypertracker_provider = HypertrackerProvider(
        redis_client=redis_client,
        http_client=hypertracker_pool._client,
    )
    _pyth_http = httpx.AsyncClient(
        timeout=httpx.Timeout(120.0, connect=10.0),
        http2=True,
    )
    pyth_connector = PythHermesConnector(
        redis_client=redis_client,
        settings=config,
        http_client=_pyth_http,
    )
    # Tier 2 — Dune MCP (no HTTP pool; SSE ephemeral connections)
    dune_provider = DuneMCPProvider(
        redis_client=redis_client,
        settings=config,
    )
    # Tier 2 — FRED REST (global macro data — no asset suffix in cache keys)
    _fred_http = httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, connect=5.0),
    )
    fred_provider = FREDProvider(
        redis_client=redis_client,
        settings=config,
        http_client=_fred_http,
    )
    # Tier 2 — Alternative.me Fear & Greed (macro sentiment)
    _alternative_me_http = httpx.AsyncClient(
        timeout=httpx.Timeout(15.0, connect=5.0),
    )
    alternative_me_provider = AlternativeMeConnector(
        redis_client=redis_client,
        settings=config,
        http_client=_alternative_me_http,
    )
    coingecko_adapter = CoinGeckoAdapter(redis_client=redis_client, config=config)
    _helius_http = httpx.AsyncClient(
        timeout=httpx.Timeout(15.0, connect=5.0),
        http2=True,
    )
    helius_provider = HeliusProvider(
        redis_client=redis_client,
        settings=config,
        http_client=_helius_http,
    )
    _deribit_options_http = httpx.AsyncClient(
        timeout=httpx.Timeout(
            config.deribit_options_timeout_seconds,
            connect=5.0,
        ),
        http2=True,
    )
    deribit_options_provider = DeribitOptionsProvider(
        redis_client=redis_client,
        settings=config,
        http_client=_deribit_options_http,
    )
    _hydra_ping_redis = redis_async.from_url(
        config.resolved_hydra_redis_url(),
        socket_timeout=5.0,
    )
    hydra_redis_ping_probe = _HydraRedisPingProbe(_hydra_ping_redis)
    health_tracker = ProviderHealthTracker(redis_client)


def get_provider(name: str) -> Any:
    """Get a provider instance by name."""
    if _config is None:
        raise RuntimeError("Registry not initialized — call initialize_registry() first")
    mapping = {
        "coinalyze": coinalyze_pool,
        "pyth": pyth_pool,
        "pyth_hermes": pyth_connector,
        "defillama": defillama_pool,
        "coinapi": coinapi_pool,
        "nansen": nansen_provider,
        "hypertracker": hypertracker_provider,
        "dune_mcp": dune_provider,
        "fred_rest": fred_provider,
        "fear_greed": alternative_me_provider,
        "coingecko": coingecko_adapter,
        "helius": helius_provider,
        "hydra": hydra_redis_ping_probe,
    }
    return mapping.get(name)


def get_registry() -> Any:
    """Return an object that mimics the registry interface for dependency injection."""
    class Registry:
        async def get_provider(self, name: str) -> Any:
            return get_provider(name)
    return Registry()


async def get_provider_health(name: str) -> float:
    """Get the health score for a single provider."""
    if health_tracker is None:
        raise RuntimeError("Registry not initialized — call initialize_registry() first")
    return await health_tracker.get_health(name)


async def get_system_health() -> dict[str, float]:
    """Get the health scores for all registered providers."""
    if _config is None or health_tracker is None or deepseek_chat_client is None or mistral_embedding_client is None or local_llm_client is None:
        raise RuntimeError("Registry not initialized — call initialize_registry() first")
        
    providers = [
        "coinalyze", "pyth", "pyth_hermes", "defillama",
        "nansen", "coinapi", "dune_mcp", "fred_rest", "hypertracker",
        "fear_greed", "coingecko", "helius", "hydra",
    ]

    scores: list[float] = [0.0] * len(providers)

    _ht = health_tracker

    async def _safe_health(idx: int, name: str) -> None:
        try:
            scores[idx] = await _ht.get_health(name)
        except Exception as e:
            from loguru import logger
            logger.error("Health check failed for {}: {}", name, e)
            scores[idx] = 0.0

    await asyncio.gather(
        *[_safe_health(i, p) for i, p in enumerate(providers)]
    )

    health_dict = dict(zip(providers, scores))

    ds_health = await deepseek_chat_client.health_check()
    mistral_health = await mistral_embedding_client.health_check()

    health_dict["deepseek"] = 1.0 if ds_health else 0.0
    health_dict["mistral"] = 1.0 if mistral_health else 0.0

    if _config.local_enabled:
        local_health = await local_llm_client.health_check()
        health_dict["local_llm"] = 1.0 if local_health else 0.0

    return health_dict
