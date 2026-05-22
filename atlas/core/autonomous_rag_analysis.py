"""Background loop: RAG-informed pipeline analysis across the active asset universe."""

from __future__ import annotations

import asyncio
import hashlib
import time
from decimal import Decimal
from typing import Any

import lancedb  # type: ignore[import-untyped]
import msgspec
import redis.asyncio as redis_async
from loguru import logger
from qdrant_client import AsyncQdrantClient

from atlas.agents.base import BaseAgent
from atlas.agents.derivatives.derivatives_agent import DerivativesAgent
from atlas.agents.onchain.onchain_agent import OnChainAgent
from atlas.agents.regime.regime_agent import MarketRegimeAgent
from atlas.agents.risk.risk_agent import RiskAgent
from atlas.agents.sentiment.sentiment_agent import SentimentNewsAgent
from atlas.agents.synthesiser.synthesiser_agent import SynthesiserAgent
from atlas.agents.technical.technical_agent import TechnicalAgent
from atlas.core.asset_universe import AssetConfig, AssetTier, get_active_assets
from atlas.core.regime_snapshots import record_regime_snapshot
from atlas.models.signal import SignalOutput
from atlas.orchestrator.deepseek_client import DeepSeekOrchestratorClient
from atlas.orchestrator.scorer import ConfluenceScorer
from atlas.pipeline.execution_gate import log_skipped_opportunity
from atlas.pipeline.orchestrator import PipelineOrchestrator
from atlas.providers.nansen.models import ExchangeNetflow, NansenSnapshot, SmartMoneyFlow
from atlas.providers.hydra.listener import HydraStreamListener
from atlas.rag.embedding_service import EmbeddingService
from atlas.rag.query import RAGQueryEngine
from atlas.rag.writer import RAGWriter
from atlas.shared.config import PolarisSettings

_SIGNAL_CACHE_TTL_S: int = 86_400
_NEXT_CYCLE_TS_KEY: str = "polaris:next_cycle_ts"
# Redis set written by POST /api/dashboard/pins — mirrors operator-pinned dashboard cards.
DASHBOARD_PINS_REDIS_KEY: str = "polaris:dashboard:pins"

_ANALYSIS_UNIVERSE_KEYS: tuple[str, ...] = (
    "polaris:universe:all",
    "polaris:rotation:active_33",
    "polaris:rotation:daily_8",
)
_FALLBACK_DASHBOARD_BASES: tuple[str, ...] = (
    "BTC",
    "ETH",
    "SOL",
    "XRP",
    "ADA",
    "AVAX",
    "DOGE",
    "DOT",
    "LINK",
    "MATIC",
    "LTC",
    "BCH",
    "APT",
    "SUI",
    "ARB",
    "OP",
    "NEAR",
    "ATOM",
    "FIL",
    "INJ",
    "RENDER",
    "TIA",
    "SEI",
    "MANA",
    "SAND",
    "AAVE",
    "SNX",
    "MKR",
    "UNI",
    "LDO",
    "CRV",
    "STX",
    "IMX",
)


def default_agent_market_data(asset: str = "BTC/USDT") -> dict[str, Any]:
    """Non-empty, deterministic fallback market payload for one asset.

    This is used only when live provider data is not yet available. It must vary
    by asset so the autonomous loop does not produce identical fallback scores
    across the whole universe.
    """
    seed = _asset_seed(asset)
    base_price = 1_000.0 + float(seed % 60_000)
    trend_per_step = ((seed % 21) - 10) / 10_000.0
    wave_scale = ((seed // 7) % 9) / 10_000.0
    close_series = [
        base_price * (1.0 + trend_per_step * idx + wave_scale * ((idx % 7) - 3))
        for idx in range(100)
    ]
    high_series = [price * 1.002 for price in close_series]
    low_series = [price * 0.998 for price in close_series]
    open_series = [close_series[0], *close_series[:-1]]
    volume_base = 1.0 + float(seed % 17) / 10.0
    volume_series = [
        volume_base * (1.0 + ((idx + seed) % 5) / 20.0)
        for idx in range(100)
    ]
    latest = close_series[-1]
    previous = close_series[-15]
    momentum_pct = ((latest - previous) / previous) * 100.0
    rsi = max(5.0, min(95.0, 50.0 + momentum_pct * 3.0))
    volume_ratio = volume_series[-1] / max(sum(volume_series[-20:]) / 20.0, 0.0001)
    funding_zscore = 1.6 + float((seed // 11) % 5) / 4.0
    oi_change_pct = 1.2 + float((seed // 13) % 9) / 2.0
    futures_basis_pct = 0.0
    oi_trend = "expanding"
    basis_signal = "neutral"
    polarity_percentile = max(0.0, min(100.0, 50.0 + momentum_pct * 8.0))
    volume_zscore = 2.0 + float((seed // 23) % 5) / 4.0
    smart_money_netflow = Decimal(1_250_000 + seed % 2_500_000)
    exchange_netflow = -Decimal(1_500_000 + seed % 3_000_000)
    nansen_snapshot = NansenSnapshot(
        asset=asset,
        smart_money_flow_24h=SmartMoneyFlow(
            asset=asset,
            chain="synthetic",
            net_flow_usd=smart_money_netflow,
            unique_smart_wallets=12 + seed % 24,
            flow_direction="ACCUMULATING",
        ),
        exchange_netflow=ExchangeNetflow(
            asset=asset,
            netflow_usd=exchange_netflow,
            outflow_usd=abs(exchange_netflow),
        ),
    )
    return {
        "asset": asset,
        "close": close_series,
        "high": high_series,
        "low": low_series,
        "open": open_series,
        "volume": volume_series,
        "volume_ratio": volume_ratio,
        "rsi_14": rsi,
        "adx": 18.0 + float(seed % 18),
        "macd_histogram": momentum_pct / 100.0,
        "bb_position": max(0.05, min(0.95, 0.5 + momentum_pct / 20.0)),
        "vwap_deviation_pct": momentum_pct / 4.0,
        "sr_nearest_resistance_pct": 5.0,
        "sr_nearest_support_pct": -5.0,
        "orderbook_imbalance": ((seed % 11) - 5) / 20.0,
        "funding_zscore": funding_zscore,
        "zscore": funding_zscore,
        "oi_change_pct": oi_change_pct,
        "oi_change_4h": oi_change_pct,
        "oi_trend": oi_trend,
        "futures_basis_pct": futures_basis_pct,
        "basis_annualised": futures_basis_pct * 365.0,
        "basis_signal": basis_signal,
        "funding_rate": ((seed // 19) % 11 - 5) / 10_000.0,
        "open_interest_usd": base_price * volume_base * 10_000.0,
        "vol_zscore": volume_zscore,
        "polarity_percentile": polarity_percentile,
        "nansen_snapshot": nansen_snapshot,
    }


def _asset_seed(asset: str) -> int:
    digest = hashlib.sha256(asset.upper().encode()).hexdigest()
    return int(digest[:8], 16)


def normalise_analysis_symbol(raw_symbol: str) -> str | None:
    """Normalize Redis / UI wire formats (``BTCUSDT``, ``BTC/USDT``) to canonical ``BASE/USDT``."""
    cleaned = raw_symbol.strip().upper()
    if not cleaned or cleaned.startswith("NO_PERP"):
        return None
    compact = cleaned.replace("-", "").replace("_", "").replace("/", "")
    if compact.endswith("USDT") and len(compact) > 4:
        return f"{compact[:-4]}/USDT"
    return f"{compact}/USDT"


def _decode_redis_symbol(raw_value: Any) -> str | None:
    if isinstance(raw_value, bytes):
        return raw_value.decode("utf-8", errors="ignore")
    if isinstance(raw_value, str):
        return raw_value
    return None


def _decode_smembers_sorted_strings(raw_result: Any) -> list[str]:
    """Normalize Redis SMEMBERS payloads into sorted UTF-8 strings."""
    if not isinstance(raw_result, (set, list, tuple)):
        return []
    decoded: list[str] = []
    for raw_value in raw_result:
        text = _decode_redis_symbol(raw_value)
        if text is not None and text.strip() != "":
            decoded.append(text)
    return sorted(decoded)


def dashboard_ladder_symbols(active_33_members: list[str], ladder_cap: int = 33) -> list[str]:
    """Mirror ``calculate_dashboard_pairs`` — Redis ladder entries plus FE fallback fill."""
    seen_symbols: set[str] = set()
    ladder: list[str] = []

    for raw_symbol in active_33_members:
        symbol = normalise_analysis_symbol(raw_symbol)
        if symbol is None or symbol.upper() in seen_symbols:
            continue
        seen_symbols.add(symbol.upper())
        ladder.append(symbol)
        if len(ladder) >= ladder_cap:
            return ladder

    for fallback_base in _FALLBACK_DASHBOARD_BASES:
        symbol = f"{fallback_base}/USDT"
        if symbol.upper() in seen_symbols:
            continue
        seen_symbols.add(symbol.upper())
        ladder.append(symbol)
        if len(ladder) >= ladder_cap:
            break

    return ladder


def prioritized_analysis_symbols(
    universe_members: list[str],
    active_33_members: list[str],
    daily_8_members: list[str],
    dashboard_pin_members: list[str],
) -> list[str]:
    """Dashboard ladder first, pinned extras, daily rotation, then remaining universe."""
    seen_symbols: set[str] = set()
    ordered: list[str] = []

    for symbol in dashboard_ladder_symbols(active_33_members):
        ordered.append(symbol)
        seen_symbols.add(symbol.upper())

    for raw_symbol in dashboard_pin_members:
        symbol = normalise_analysis_symbol(raw_symbol)
        if symbol is None or symbol.upper() in seen_symbols:
            continue
        seen_symbols.add(symbol.upper())
        ordered.append(symbol)

    for raw_symbol in daily_8_members:
        symbol = normalise_analysis_symbol(raw_symbol)
        if symbol is None or symbol.upper() in seen_symbols:
            continue
        seen_symbols.add(symbol.upper())
        ordered.append(symbol)

    for raw_symbol in universe_members:
        symbol = normalise_analysis_symbol(raw_symbol)
        if symbol is None or symbol.upper() in seen_symbols:
            continue
        seen_symbols.add(symbol.upper())
        ordered.append(symbol)

    return ordered


def asset_configs_for_normalized_order(ordered_symbols: list[str]) -> list[AssetConfig]:
    """Map canonical ``BASE/USDT`` symbols (already ordered and deduped) to configs."""
    existing_assets = {asset.symbol.upper(): asset for asset in get_active_assets()}
    seen_symbols: set[str] = set()
    configs: list[AssetConfig] = []

    for symbol in ordered_symbols:
        key = symbol.upper()
        if key in seen_symbols:
            continue
        seen_symbols.add(key)
        configs.append(
            existing_assets.get(
                key,
                AssetConfig(
                    symbol=symbol,
                    tier=AssetTier.ROTATION,
                    group="dashboard",
                    has_perp=True,
                ),
            )
        )

    return configs


def calculate_analysis_asset_configs(raw_symbols: list[str]) -> list[AssetConfig]:
    """Build the score universe from Redis rotation lists plus dashboard fallback."""
    existing_assets = {asset.symbol.upper(): asset for asset in get_active_assets()}
    ordered_symbols: list[str] = []
    seen_symbols: set[str] = set()

    for raw_symbol in raw_symbols:
        symbol = normalise_analysis_symbol(raw_symbol)
        if symbol is None or symbol.upper() in seen_symbols:
            continue
        seen_symbols.add(symbol.upper())
        ordered_symbols.append(symbol)

    for fallback_base in _FALLBACK_DASHBOARD_BASES:
        symbol = f"{fallback_base}/USDT"
        if symbol.upper() in seen_symbols:
            continue
        seen_symbols.add(symbol.upper())
        ordered_symbols.append(symbol)

    return [
        existing_assets.get(
            symbol.upper(),
            AssetConfig(
                symbol=symbol,
                tier=AssetTier.ROTATION,
                group="dashboard",
                has_perp=True,
            ),
        )
        for symbol in ordered_symbols
    ]


def _build_default_agents(
    redis_client: redis_async.Redis,  # type: ignore[type-arg]
    hydra: HydraStreamListener,
) -> list[BaseAgent]:
    return [
        TechnicalAgent(),
        DerivativesAgent(),
        OnChainAgent(),
        SentimentNewsAgent(),
        MarketRegimeAgent(),
        RiskAgent(redis_client, hydra),
    ]


async def build_rag_query_engine(
    settings: PolarisSettings,
    embedding_service: EmbeddingService,
) -> RAGQueryEngine | None:
    """Construct RAGQueryEngine when Qdrant and LanceDB are reachable."""
    client: AsyncQdrantClient | None = None
    try:
        from atlas.core.qdrant_client_factory import create_async_qdrant_client

        client = create_async_qdrant_client(settings, timeout=5)
        await asyncio.wait_for(client.get_collections(), timeout=5.0)
        lance_conn = lancedb.connect(settings.lancedb_uri)
        return RAGQueryEngine(
            settings,
            client,
            lance_conn,
            embedding_service,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning(
            "rag_query_engine_unavailable | err={}",
            str(exc),
        )
        if client is not None:
            await client.close()
        return None


class AutonomousRAGAnalysisRunner:
    """Runs PipelineOrchestrator on each active asset on a fixed interval."""

    def __init__(
        self,
        redis_client: redis_async.Redis,  # type: ignore[type-arg]
        settings: PolarisSettings,
        rag_engine: RAGQueryEngine | None,
        rag_writer: RAGWriter | None,
        interval_s: int | None = None,
    ) -> None:
        self._redis = redis_client
        self._settings = settings
        hydra_url = settings.resolved_hydra_redis_url()
        self._hydra_redis_url_display = hydra_url
        self._hydra_redis_owned = hydra_url != settings.redis_url
        if self._hydra_redis_owned:
            self._hydra_redis = redis_async.from_url(
                hydra_url,
                decode_responses=False,
            )
        else:
            self._hydra_redis = redis_client
        self._rag_engine = rag_engine
        self._rag_writer = rag_writer
        self._interval_s = (
            interval_s
            if interval_s is not None
            else settings.confluence_cycle_interval_seconds
        )
        self._hydra = HydraStreamListener(self._hydra_redis)
        self._deepseek = DeepSeekOrchestratorClient(settings)
        scorer = ConfluenceScorer(settings=settings)
        synthesiser = SynthesiserAgent(scorer)
        agents = _build_default_agents(redis_client, self._hydra)
        self._agents = agents
        self._orchestrator = PipelineOrchestrator(
            agents,
            synthesiser,
            settings,
            redis_client,
            rag_engine,
            deepseek_client=self._deepseek,
        )
        self._running = False
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._prime_agents_for_live_analysis()
        await self._hydra.start()
        self._task = asyncio.create_task(self._loop())
        logger.info(
            "autonomous_rag_analysis_started | interval_s={} | rag_engine={} | hydra_redis={}",
            self._interval_s,
            self._rag_engine is not None,
            self._hydra_redis_url_display,
        )

    async def close(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                current_task = asyncio.current_task()
                cancelling = getattr(current_task, "cancelling", lambda: 0)
                if current_task is not None and cancelling():
                    raise
            self._task = None
        await self._hydra.close()
        if self._hydra_redis_owned:
            await self._hydra_redis.aclose()
        if self._rag_engine is not None:
            try:
                await self._rag_engine.aclose()
            except Exception as exc:
                logger.warning("rag_engine_close_failed | err={}", str(exc))
        await self._deepseek.close()
        logger.info("autonomous_rag_analysis_stopped")

    async def _publish_next_cycle_deadline(self) -> None:
        """Write unix time of next scoring boundary for ``read_system`` countdown."""
        try:
            next_ts = time.time() + float(self._interval_s)
            ttl_s = int(self._interval_s) + 300
            await asyncio.wait_for(
                self._redis.set(_NEXT_CYCLE_TS_KEY, str(next_ts).encode("utf-8"), ex=ttl_s),
                timeout=2.0,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("next_cycle_ts_publish_failed | err={}", str(exc))

    def _prime_agents_for_live_analysis(self) -> None:
        """Move agents through warmup so manual dashboard scans produce analysis.

        The autonomous trader is launched on demand from the UI. Without a
        historical sampler running ahead of it, agents would stay in WARMING_UP
        forever because warmup results intentionally skip scoring. Priming here
        keeps the safety gate explicit while allowing immediate paper-mode
        analysis from the dashboard.
        """
        for agent in self._agents:
            while not agent.is_ready:
                agent.record_sample()

    async def fetch_analysis_assets(self) -> list[AssetConfig]:
        """Read the Redis-backed dashboard universe that needs score snapshots."""
        try:
            pipeline = self._redis.pipeline()
            for key in _ANALYSIS_UNIVERSE_KEYS:
                pipeline.smembers(key)
            raw_results = await asyncio.wait_for(pipeline.execute(), timeout=5.0)
            key_count = len(_ANALYSIS_UNIVERSE_KEYS)
            if not isinstance(raw_results, list) or len(raw_results) != key_count:
                logger.warning(
                    "analysis_asset_universe_unexpected_pipeline_rows | rows={}",
                    len(raw_results) if isinstance(raw_results, list) else -1,
                )
                return calculate_analysis_asset_configs([])

            universe_raw = _decode_smembers_sorted_strings(raw_results[0])
            active_raw = _decode_smembers_sorted_strings(raw_results[1])
            daily_raw = _decode_smembers_sorted_strings(raw_results[2])

            pins_sorted: list[str] = []
            try:
                pins_raw = await asyncio.wait_for(
                    self._redis.smembers(DASHBOARD_PINS_REDIS_KEY),
                    timeout=5.0,
                )
                pins_sorted = _decode_smembers_sorted_strings(pins_raw)
            except asyncio.CancelledError:
                raise
            except Exception as pin_exc:
                logger.warning(
                    "dashboard_pins_fetch_failed | err={}",
                    str(pin_exc),
                )

            ordered_symbols = prioritized_analysis_symbols(
                universe_raw,
                active_raw,
                daily_raw,
                pins_sorted,
            )
            return asset_configs_for_normalized_order(ordered_symbols)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "analysis_asset_universe_fetch_failed | err={}",
                str(exc),
            )
            return calculate_analysis_asset_configs([])

    async def _loop(self) -> None:
        while self._running:
            try:
                try:
                    halted_flag = await asyncio.wait_for(
                        self._redis.exists("prometheus:trading_halted"),
                        timeout=3.0,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    halted_flag = 0
                if halted_flag:
                    logger.warning(
                        "autonomous_cycle_skipped_trading_halted | interval_s={}",
                        self._interval_s,
                    )
                    await asyncio.sleep(min(60.0, float(self._interval_s)))
                    continue

                for asset_cfg in await self.fetch_analysis_assets():
                    if not self._running:
                        break
                    symbol = asset_cfg.symbol
                    data = default_agent_market_data(symbol)
                    try:
                        signal = await self._orchestrator.run(
                            data=data,
                            context=data,
                            asset=symbol,
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logger.exception("autonomous_cycle_failed | asset={}", symbol)
                        continue
                    await self._persist_outputs(signal)
                await self._publish_next_cycle_deadline()
                await asyncio.sleep(self._interval_s)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("autonomous_analysis_loop_error")
                await asyncio.sleep(5)

    async def _read_bitget_btc_spot_price(self) -> str | None:
        """Best-effort BTC mark for regime timeline overlays (display-only)."""
        try:
            raw_b = await asyncio.wait_for(
                self._redis.get("provider:bitget:price:BTC"),
                timeout=2.0,
            )
            if not raw_b:
                return None
            decoded = msgspec.json.decode(raw_b)
            if isinstance(decoded, dict):
                px = decoded.get("price")
                if px is None:
                    return None
                text = str(px).strip()
                return text if text else None
        except asyncio.CancelledError:
            raise
        except Exception:
            return None
        return None

    async def _persist_outputs(self, signal: SignalOutput) -> None:
        key = f"polaris:signals:{signal.asset}"
        dump = signal.model_dump(mode="json")
        try:
            raw = msgspec.json.encode(dump)
            await asyncio.wait_for(
                self._redis.setex(key, _SIGNAL_CACHE_TTL_S, raw),
                timeout=5.0,
            )
            await asyncio.wait_for(
                self._redis.setex("polaris:latest_signal", _SIGNAL_CACHE_TTL_S, raw),
                timeout=5.0,
            )
        except Exception as exc:
            logger.error(
                "signal_cache_failed | asset={} | err={}",
                signal.asset,
                str(exc),
            )

        if signal.score < self._settings.min_trade_score:
            if "BELOW_MIN_TRADE_SCORE" not in signal.key_risks:
                log_skipped_opportunity(
                    asset=signal.asset,
                    timestamp=signal.timestamp,
                    score=int(signal.score),
                    reason="autonomous_persist_suppressed_execution_fanout",
                    min_trade_score=int(self._settings.min_trade_score),
                    raw_confluence_score=int(signal.raw_confluence_score),
                )
            return

        if self._rag_writer is not None:
            try:
                await self._rag_writer.write_signal_context(signal)
            except Exception as exc:
                logger.error(
                    "rag_writer_failed | asset={} | err={}",
                    signal.asset,
                    str(exc),
                )

        try:
            channel = f"polaris:signals:{signal.asset}"
            payload = msgspec.json.encode(dump)
            await asyncio.wait_for(
                self._redis.publish(channel, payload),
                timeout=5.0,
            )
        except Exception as exc:
            logger.error(
                "polaris_signal_publish_failed | asset={} | err={}",
                signal.asset,
                str(exc),
            )

        btc_price = await self._read_bitget_btc_spot_price()
        await record_regime_snapshot(self._redis, signal_dict=dump, btc_price=btc_price)
