"""PolarisSettings — centralised configuration via pydantic-settings.

All environment variables are read through this class.
The ``.env`` file is loaded automatically when
present. Settings are frozen after construction.

Architecture note:
    Session 00 defines the minimal settings needed for signal TTL
    and Redis connectivity. Future sessions extend with provider
    API keys, model routing, and feature flags.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from pydantic import Field, SecretStr, AliasChoices
from pydantic_settings import BaseSettings, SettingsConfigDict


class CQRConfig(BaseSettings):
    """Conformal / CQR calibration gates — Phase 1 metadata only (no scoring change)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="ATLAS_CQR_",
        extra="ignore",
        frozen=True,
    )

    enabled: bool = False
    min_calibration_samples: int = 500
    coverage_level: Decimal = Field(
        default=Decimal("0.10"),
        description="Target miscoverage (alpha); reserved for future CQR quantiles",
    )
    cqr_rollout_stage: str = Field(
        default="SHADOW",
        pattern="^(SHADOW|ACTIVE_TIGHT_ONLY|FULL)$",
        description=(
            "Graduated rollout stage: SHADOW (log only), "
            "ACTIVE_TIGHT_ONLY (leverage when width<20), FULL (all bounds)"
        ),
    )


class ReflectionConfig(BaseSettings):
    """Reflection critic tuning and hard-guard configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="ATLAS_REFLECTION_",
        extra="ignore",
        frozen=True,
    )

    enabled: bool = False
    score_threshold: int = 160
    deq_timeout_s: float = 5.0
    deq_max_rounds: int = 2
    convergence_pts: int = 11
    wbft_divergence_pts: int = 25
    model: str = "deepseek-chat"
    shadow_mode: bool = True
    max_score_reduction: int = 15
    min_score_floor: int = 55


class CanaryConfig(BaseSettings):
    """ML Canary deployment configuration.

    Controls shadow → canary_10pct → canary_50pct → live promotion
    pipeline with automatic rollback on performance degradation.
    Disabled by default.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="ATLAS_CANARY_",
        extra="ignore",
        frozen=True,
    )

    enabled: bool = False
    shadow_cycles_required: int = 500
    classification_agreement_threshold: float = 0.95
    regression_mae_tolerance: float = 0.10
    canary_sharpe_degradation_pct: float = 0.10
    canary_drawdown_degradation_pct: float = 0.10
    canary_10pct_duration_days: int = 7
    canary_50pct_duration_days: int = 7
    rolling_window_hours: int = 4


class ModelStackConfig(BaseSettings):
    """Model stack configuration — all LLM and embedding provider settings.

    Every model reference in ATLAS reads from this config.
    Secrets are `SecretStr` — call `.get_secret_value()` only at the
    point of sending the actual HTTP request. Never log SecretStr values.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="",
        case_sensitive=False,
        extra="ignore",
    )

    # ─── EMBEDDING PROVIDER ───────────────────────────────
    embed_provider: str = Field(
        default="mistral",
        description='Set to "lmstudio" to use LM Studio OpenAI-compatible /v1/embeddings (see lmstudio_base_url).',
    )
    embed_model: str = "mistral-embed"
    embed_endpoint: str = "https://api.mistral.ai/v1/embeddings"
    embed_api_key: SecretStr = Field(default=SecretStr(""), alias="MISTRAL_API_KEY")
    embed_dimensions: int = 1024
    embed_batch_size: int = 32
    embed_timeout_s: float = 30.0

    # ─── DEEPSEEK (primary LLM) ──────────────────────────
    deepseek_api_key: SecretStr = Field(default=SecretStr(""), alias="DEEPSEEK_API_KEY")
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_chat_model: str = "deepseek-chat"
    deepseek_reasoner_model: str = "deepseek-reasoner"
    deepseek_max_tokens: int = 4096
    deepseek_timeout_s: float = 60.0

    # ─── LOCAL MODEL (not yet running) ────────────────────
    local_enabled: bool = False
    local_url: str = "http://localhost:8080"
    local_model_name: str = ""
    local_max_tokens: int = 2048
    local_timeout_s: float = 30.0
    lmstudio_base_url: str = "http://localhost:1234/v1"

    omnibox_chat_backend: str = Field(
        default="lmstudio",
        description=(
            'OmniBox completions: set to "deepseek" for cloud DeepSeek; '
            'any other value (default "lmstudio") uses LM Studio at lmstudio_base_url.'
        ),
    )
    omnibox_chat_model: str = Field(
        default="",
        description=(
            "Explicit LM Studio/OpenAI-compat model id for OmniBox when "
            "omnibox_chat_backend is lmstudio. When empty: router_local_model "
            "then router_api_model."
        ),
    )

    # ─── COMPLEXITY ROUTER ────────────────────────────────
    router_routine_provider: str = "deepseek"
    router_highstakes_provider: str = "deepseek"
    router_fallback_provider: str = "deepseek"
    router_escalation_confluence: int = 140
    router_escalation_on_anomaly: bool = True

    # LLM Router specific keys (S3-P8)
    router_local_model: str = "deepseek-v3"
    router_api_model: str = "deepseek-r1"
    router_api_endpoint: str = "https://api.deepseek.com/v1/chat/completions"
    router_max_tokens: int = 2048
    router_temperature: float = 0.1

    # 3-Tier Gating thresholds
    router_gated_threshold: int = 140
    router_mandatory_threshold: int = 170
    router_gated_confidence_threshold: float = 0.75


class PolarisSettings(ModelStackConfig):
    """Centralised ATLAS configuration.

    Reads from environment variables and ``.env`` file. All downstream
    code imports this class.

    Attributes:
        redis_url: Connection string for Redis (default localhost).
        signal_ttl_minutes: Mapping of timeframe to signal TTL in minutes.
        cascade_signal_ttl_minutes: Override TTL for cascade-triggered
            signals (2 minutes gives PROMETHEUS enough headroom to queue,
            validate via RiskEnforcer, submit to Bitget, and receive fill
            confirmation without the signal going stale mid-pipeline).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    atlas_expose_openapi: bool = Field(
        default=False,
        description=(
            "When true, serve /docs, /redoc, and /openapi.json on the ASGI app. "
            "Keep false in production so the route catalogue is not public."
        ),
        validation_alias=AliasChoices("ATLAS_EXPOSE_OPENAPI"),
    )

    # --- Redis & Postgres ---
    redis_url: str = "redis://localhost:6379/0"
    redis_socket_connect_timeout_s: float = Field(
        default=2.5,
        ge=0.5,
        le=15.0,
        description="TCP connect timeout for redis.asyncio (fail-fast vs default ~30s hangs).",
        validation_alias=AliasChoices(
            "REDIS_SOCKET_CONNECT_TIMEOUT_S",
            "ATLAS_REDIS_SOCKET_CONNECT_TIMEOUT_S",
        ),
    )
    redis_socket_timeout_s: float = Field(
        default=2.5,
        ge=0.5,
        le=15.0,
        description="Per-command socket read/write timeout for redis.asyncio.",
        validation_alias=AliasChoices(
            "REDIS_SOCKET_TIMEOUT_S",
            "ATLAS_REDIS_SOCKET_TIMEOUT_S",
        ),
    )
    redis_startup_probe_timeout_s: float = Field(
        default=3.0,
        ge=1.0,
        le=60.0,
        description="Wall-clock cap for the startup Redis probe (SET/GET/INFO + version gate).",
        validation_alias=AliasChoices(
            "REDIS_STARTUP_PROBE_TIMEOUT_S",
            "ATLAS_REDIS_STARTUP_PROBE_TIMEOUT_S",
        ),
    )
    redis_reconnect_backoff_initial_s: float = Field(
        default=1.0,
        ge=0.2,
        le=120.0,
        description="First delay between background reconnect attempts when using in-memory fallback.",
        validation_alias=AliasChoices(
            "REDIS_RECONNECT_BACKOFF_INITIAL_S",
            "ATLAS_REDIS_RECONNECT_BACKOFF_INITIAL_S",
        ),
    )
    redis_reconnect_backoff_max_s: float = Field(
        default=60.0,
        ge=1.0,
        le=600.0,
        description="Maximum backoff delay for Redis background reconnect.",
        validation_alias=AliasChoices(
            "REDIS_RECONNECT_BACKOFF_MAX_S",
            "ATLAS_REDIS_RECONNECT_BACKOFF_MAX_S",
        ),
    )
    hydra_redis_url: str = Field(
        default="redis://127.0.0.1:6380/1",
        description=(
            "Redis URL for HYDRA streams and hydra:* keys (HYDRA docker-compose: "
            "host port 6380, logical DB 1). Set HYDRA_REDIS_URL empty or identical "
            "to REDIS_URL when HYDRA shares the ATLAS Redis instance."
        ),
        validation_alias=AliasChoices("HYDRA_REDIS_URL", "ATLAS_HYDRA_REDIS_URL"),
    )
    postgres_url: str = Field(
        default="postgresql://postgres:postgres@127.0.0.1:5433/atlas",
        description=(
            "Postgres for Polaris/RAG. docker-compose.dev.yml publishes atlas-postgres-dev "
            "on host port 5433 (container 5432). Override with POSTGRES_URL in .env."
        ),
        validation_alias=AliasChoices("POSTGRES_URL"),
    )
    dead_letter_threshold_seconds: int = 300

    # Paper-trade — ATLAS side reads cache + publishes instructions only.
    paper_trade_redis_channel: str = "polaris:paper_trade"
    paper_trade_ack_wait_seconds: float = 2.0   # max time to wait for executor ack
    paper_trade_symbol_cache_key: str = "polaris:demo_symbols:cache"

    backtest_duckdb_path: Path = Field(
        default=Path("prometheus/backtest/polaris_backtest.duckdb"),
        description="DuckDB warehouse for offline candle + signal replay (dashboard backtest suite).",
        validation_alias=AliasChoices(
            "ATLAS_BACKTEST_DUCKDB_PATH",
            "POLARIS_BT_DB_PATH",
        ),
    )

    reflection: ReflectionConfig = Field(default_factory=ReflectionConfig)
    cqr: CQRConfig = Field(default_factory=CQRConfig)
    canary: CanaryConfig = Field(default_factory=CanaryConfig)

    # --- Latency Budgets (Phase 5) ---
    latency_budgets: dict[str, float] = {
        "data_ingestion": 0.5,
        "validation_gate": 0.05,
        "agent_ensemble": 5.0,
        "confluence_scoring": 0.1,
        "signal_emission": 0.01,
        "order_submission": 0.2,
    }

    # --- Signal TTL ---
    signal_ttl_minutes: dict[str, int] = {
        "15m": 15,
        "30m": 30,
        "1h": 60,
        "4h": 240,
        "1d": 1440,
    }

    # 2 minutes gives PROMETHEUS enough headroom to queue, validate
    # (RiskEnforcer), submit to Bitget, and receive fill confirmation
    # without the signal going stale mid-pipeline. A 1-minute TTL was
    # rejected as too aggressive — if PROMETHEUS is momentarily busy
    # with another order or kill-switch evaluation, a 60s budget
    # leaves no margin for processing jitter.
    cascade_signal_ttl_minutes: int = 2

    # Seconds between full autonomous pipeline passes (confluence scoring per asset).
    # Default 900 (15 minutes). Override with ATLAS_CONFLUENCE_CYCLE_INTERVAL_SECONDS.
    confluence_cycle_interval_seconds: int = Field(
        default=900,
        ge=15,
        le=86_400,
        description=(
            "Interval between autonomous confluence cycles over the analysis universe."
        ),
        validation_alias=AliasChoices("ATLAS_CONFLUENCE_CYCLE_INTERVAL_SECONDS"),
    )

    # --- Vector DBs & RAG ---
    qdrant_url: str = Field(
        default="http://localhost:6333",
        validation_alias=AliasChoices("QDRANT_URL"),
    )
    qdrant_api_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("QDRANT_API_KEY"),
        description="Qdrant Cloud or secured local instance API key.",
    )
    lancedb_uri: str = "data/lancedb"

    # --- Freshness Decay ---
    # Per-hour exponential decay constant.
    # 0.05/hr → 2h weight 0.90, 12h weight 0.55, 24h weight 0.30, 48h weight 0.09.
    # Tune after backtesting; too aggressive (> 0.10) zeroes day-old regime context.
    rag_freshness_enabled: bool = True
    rag_freshness_lambda_per_hour: float = 0.05

    # --- Adaptive Retrieval Depth ---
    rag_shallow_top_k: int = 3
    rag_default_top_k: int = 10
    rag_deep_top_k: int = 15

    # Qdrant hnsw_ef tuning per depth — higher ef = better recall, slower
    rag_hnsw_ef_shallow: int = 50
    rag_hnsw_ef_default: int = 100
    rag_hnsw_ef_deep: int = 150

    # --- Thompson Sampling ---
    thompson_live: bool = False

    # --- RLVR (Session 19) ---
    rlvr_live: bool = False
    marl_enabled: bool = False

    # --- DeFi Llama MCP ---
    defillama_mcp_url: str = "https://mcp.defillama.com/mcp"
    defillama_ttl_tvl_seconds: int = 300
    defillama_ttl_stablecoin_seconds: int = 300
    defillama_min_tvl_usd: float = 1_000_000.0

    # DeepSeek orchestrator overrides (extends parent ModelStackConfig)
    # deepseek_api_key inherited from ModelStackConfig with DEEPSEEK_API_KEY alias
    deepseek_model: str = "deepseek-chat"
    deepseek_base_url: str = "https://api.deepseek.com/v1/chat/completions"
    deepseek_timeout_seconds: float = 30.0
    deepseek_connect_timeout_seconds: float = 5.0
    deepseek_max_concurrent: int = 10
    deepseek_temperature: float = 0.1

    # --- Langfuse Observability ---
    langfuse_secret_key: SecretStr = Field(
        default=SecretStr("sk-lf-polaris-dev"), alias="LANGFUSE_SECRET_KEY"
    )
    langfuse_public_key: str = Field(
        default="pk-lf-polaris-dev", alias="LANGFUSE_PUBLIC_KEY"
    )
    langfuse_host: str = Field(default="http://localhost:3001", alias="LANGFUSE_HOST")

    # ─── CONFIDENCE GATE (Session 20) ─────────────────────────────────
    confidence_gate_skip_threshold: float = 0.40
    confidence_gate_reduced_threshold: float = 0.65
    confidence_gate_human_conviction_min: int = 140
    confidence_gate_human_confidence_max: float = 0.50

    # ─── EXECUTION SCORE GATE ─────────────────────────────────────────
    min_trade_score: int = Field(
        default=68,
        ge=0,
        le=100,
        description=(
            "Minimum normalised confluence score (0–100) before portfolio sizing, "
            "reflection, DeepSeek, or any live execution / order-routing fan-out."
        ),
        validation_alias=AliasChoices(
            "min_trade_score",
            "ATLAS_MIN_TRADE_SCORE",
            "MIN_TRADE_SCORE",
        ),
    )

    # ─── BITGET EXCHANGE (PROMETHEUS only — Session 22B) ──────────────
    bitget_api_key: SecretStr = Field(
        default=SecretStr(""),
        alias="BITGET_API_KEY",
        description="Bitget V2 REST API key",
    )
    bitget_api_secret: SecretStr = Field(
        default=SecretStr(""),
        alias="BITGET_API_SECRET",
        description="Bitget V2 API secret (HMAC-SHA256 signing)",
    )
    bitget_api_passphrase: SecretStr = Field(
        default=SecretStr(""),
        alias="BITGET_API_PASSPHRASE",
        description="Bitget V2 API passphrase",
    )
    bitget_base_url: str = Field(
        default="https://api.bitget.com",
        description="Bitget REST API base URL",
    )
    paper_trading: bool = Field(
        default=True,
        description="Safe default — must be explicitly set to False for live trading",
    )

    # ─── AGENT ZERO (Session 24) ─────────────────────────────────────
    agent_zero_threshold: float = Field(
        default=0.75,
        description="Escore threshold — documents below this are soft-deleted",
    )
    agent_zero_hour_utc: int = Field(
        default=2,
        description="UTC hour when Agent Zero runs nightly (0-23)",
    )
    agent_zero_job_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("ATLAS_AGENT_ZERO_JOB_ENABLED"),
        description=(
            "Background scheduler: nightly Escore archival for RAG signal_history."
        ),
    )
    recursive_verify_enabled: bool = Field(
        default=False,
        alias="ATLAS_RECURSIVE_VERIFY_ENABLED",
        description="Enables recursive contradiction verification on Document states",
    )

    # ─── DERIVATIVES SCORING (Session 25 — v2.3) ────────────────────
    derivatives_max_points: int = Field(
        default=50,
        description="Maximum score for derivatives agent (v2.3)",
    )
    total_points: int = Field(
        default=220,
        description="Total normalisation denominator: 55+50+35+40+40=220",
    )

    # ─── OKX MCP PROVIDER ───────────────────────────────────────────
    okx_mcp_url: str = Field(
        default="https://mcp.okx.com",
        description="OKX MCP server endpoint",
    )

    # ─── DUNE MCP PROVIDER (Tier 2) ─────────────────────────────────
    dune_mcp_url: str = Field(
        default="http://localhost:8000",
        description="Dune MCP server endpoint",
    )
    dune_ttl_seconds: int = 300
    dune_query_timeout_seconds: int = 30

    # ─── FRED REST PROVIDER (Tier 2) ────────────────────────────────
    fred_api_key: SecretStr = Field(
        default=SecretStr(""),
        alias="FRED_API_KEY",
        description="FRED API Key",
    )
    fred_base_url: str = "https://api.stlouisfed.org"
    fred_ttl_seconds: int = 86400
    fred_observation_limit: int = 10
    fred_rate_limit_per_minute: int = 120

    # ─── NANSEN MCP PROVIDER (Tier 2) ───────────────────────────────
    nansen_api_key: SecretStr = Field(
        default=SecretStr(""),
        alias="NANSEN_API_KEY",
        description="Nansen credit-based API key (accessed via MCP)",
    )
    nansen_mcp_url: str = Field(
        default="https://mcp.nansen.ai/ra/mcp",
        description="Nansen MCP server endpoint",
    )
    nansen_ttl_seconds: int = 300
    nansen_query_timeout_seconds: int = 20
    nansen_smart_money_min_usd: Decimal = Field(
        default=Decimal("100000.0"),
        description="Minimum wallet size to qualify for smart money signal",
    )

    # ─── PYTH HERMES SSE PROVIDER (Tier 1 — Price Oracle) ────────────
    pyth_hermes_url: str = Field(
        default="https://hermes.pyth.network",
        description="Pyth Hermes base URL for SSE price streaming",
    )
    pyth_price_ttl_seconds: int = Field(
        default=10,
        description="Redis TTL for cached Pyth prices (seconds)",
    )
    pyth_reconnect_max_seconds: int = Field(
        default=30,
        description="Maximum exponential backoff cap for SSE reconnection",
    )

    # ─── COINALYZE (Tier 1 — one API key per endpoint family) ──────
    coinalyze_key_funding: str = ""
    coinalyze_key_oi: str = ""
    coinalyze_key_liquidations: str = ""
    coinalyze_key_long_short: str = ""

    # ─── COINGECKO / GECKO TERMINAL (validation + universe metadata) ─
    coingecko_api_key: SecretStr = Field(
        default=SecretStr(""),
        alias="COINGECKO_API_KEY",
        description="CoinGecko Pro / demo API key (optional; raises free-tier rate limits).",
    )
    coingecko_api_key_2: SecretStr = Field(
        default=SecretStr(""),
        alias="COINGECKO_2_API_KEY",
        description=(
            "Second CoinGecko key — used only after primary is rate-limited (429) "
            "in the hourly market monitor."
        ),
    )

    # ─── HELIUS (Tier 2 — Solana on-chain, SOL/JUP only) ─────────────
    helius_api_key: SecretStr = Field(
        default=SecretStr(""),
        alias="HELIUS_API_KEY",
        description="Helius API key for Solana enhanced transactions and webhooks",
    )
    helius_webhook_secret: SecretStr = Field(
        default=SecretStr(""),
        alias="HELIUS_WEBHOOK_SECRET",
        description="Shared secret verified on POST /api/webhooks/helius",
    )
    helius_webhook_url: str = Field(
        default="",
        alias="HELIUS_WEBHOOK_URL",
        description="Public URL for Helius webhook registration (setup script)",
    )
    helius_flow_poll_enabled: bool = Field(
        default=True,
        alias="HELIUS_FLOW_POLL_ENABLED",
        description="Background poll of Solana exchange addresses when webhooks are offline",
    )
    helius_flow_poll_interval_seconds: int = Field(
        default=900,
        ge=60,
        alias="HELIUS_FLOW_POLL_INTERVAL_SECONDS",
        description="Seconds between Helius exchange-flow reconciliation polls",
    )

    # ─── DERIBIT OPTIONS (Tier 2, public REST) ───────────────────────
    deribit_options_base_url: str = Field(
        default="https://www.deribit.com/api/v2/public",
        alias="DERIBIT_OPTIONS_BASE_URL",
        description="Deribit public REST base URL for options market data",
    )
    deribit_options_ttl_seconds: int = Field(
        default=300,
        ge=60,
        alias="DERIBIT_OPTIONS_TTL_SECONDS",
        description="Redis TTL for cached Deribit options snapshots",
    )
    deribit_options_timeout_seconds: float = Field(
        default=10.0,
        ge=1.0,
        alias="DERIBIT_OPTIONS_TIMEOUT_SECONDS",
        description="HTTP timeout for Deribit public API calls",
    )

    # ─── ALTERNATIVE.ME FEAR & GREED (Tier 2) ────────────────────────
    alternative_me_base_url: str = "https://api.alternative.me/fng/"
    alternative_me_ttl_seconds: int = Field(
        default=900,
        validation_alias=AliasChoices(
            "FEAR_GREED_TTL_SECONDS",
            "ALTERNATIVE_ME_TTL_SECONDS",
        ),
        description="Redis TTL for cached F&G snapshot (seconds); default 15 minutes",
    )

    # ─── WEBSOCKET SETTINGS (FBI-01) ─────────────────────────────────
    WS_PING_INTERVAL_S: int = 20
    WS_PING_TIMEOUT_S: int = 10
    WS_MAX_CONNECTIONS_PER_CHANNEL: int = 50

    # ─── SIGNAL SETTINGS (FBI-01) ────────────────────────────────────
    SIGNAL_STALE_AFTER_SECONDS: int = 300
    ACTIVE_SYMBOLS_REDIS_KEY: str = "polaris:active_symbols"

    # ─── SIMPLE PIPELINE (paper validation — deterministic confluence only) ─
    simple_pipeline_mode: bool = Field(
        default=False,
        validation_alias=AliasChoices("ATLAS_SIMPLE_PIPELINE_MODE"),
        description=(
            "Bypass parallel analyst agents; score via ConfluenceScoringEngine + RiskAgent "
            "only (DeepSeek/reflection overlays skipped)."
        ),
    )

    # ─── Horizon outcome backfill (Decision Journal / MCP feedback) ────
    outcome_horizon_job_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("ATLAS_OUTCOME_HORIZON_JOB_ENABLED"),
        description=(
            "Background loop: fills metadata outcome_pct_1h/4h/24h from price_snapshots."
        ),
    )
    outcome_horizon_job_interval_seconds: int = Field(
        default=900,
        ge=120,
        le=86_400,
        validation_alias=AliasChoices("ATLAS_OUTCOME_HORIZON_JOB_INTERVAL_SECONDS"),
    )
    price_snapshots_table: str = Field(
        default="price_snapshots",
        validation_alias=AliasChoices("ATLAS_PRICE_SNAPSHOTS_TABLE"),
        description="Postgres table with columns (sampled_at, asset, close_px).",
    )

    # ─── Hourly market monitor (daily-8 basket, CoinGecko + Pyth) ─────
    hourly_market_monitor_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("ATLAS_HOURLY_MARKET_MONITOR_ENABLED"),
        description="Background loop: hourly ingest + analytics for polaris:rotation:daily_8.",
    )
    hourly_market_monitor_interval_seconds: int = Field(
        default=3600,
        ge=600,
        le=86_400,
        validation_alias=AliasChoices("ATLAS_HOURLY_MARKET_MONITOR_INTERVAL_SECONDS"),
    )
    hourly_monitor_http_timeout_s: float = Field(default=10.0, ge=2.0, le=60.0)
    hourly_monitor_retry_max_attempts: int = Field(default=4, ge=1, le=8)
    hourly_monitor_retry_base_delay_s: float = Field(default=0.5, ge=0.1, le=10.0)
    hourly_monitor_retry_max_delay_s: float = Field(default=8.0, ge=1.0, le=60.0)
    hourly_monitor_key_cooldown_s: float = Field(default=120.0, ge=10.0, le=900.0)
    hourly_monitor_lkg_ttl_seconds: int = Field(default=7200, ge=300, le=86_400)
    hourly_monitor_redis_ttl_seconds: int = Field(default=7200, ge=300, le=86_400)
    hourly_monitor_volatility_window: int = Field(default=24, ge=4, le=168)
    hourly_monitor_price_divergence_pct: float = Field(default=0.5, ge=0.05, le=5.0)
    # Legacy env names kept for compatibility; selection is priority failover, not weighted.
    hourly_monitor_coingecko_key_weight_primary: float = Field(default=0.6, ge=0.05, le=1.0)
    hourly_monitor_coingecko_key_weight_secondary: float = Field(default=0.4, ge=0.05, le=1.0)
    hourly_monitor_alert_return_pct_warning: float = Field(default=3.0, ge=0.5, le=50.0)
    hourly_monitor_alert_return_pct_critical: float = Field(default=8.0, ge=1.0, le=100.0)
    hourly_monitor_alert_volume_z_warning: float = Field(default=2.5, ge=1.0, le=10.0)

    # ─── CoinGecko community fundamentals (cold path) ────────────────
    community_snapshot_job_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("ATLAS_COMMUNITY_SNAPSHOT_JOB_ENABLED"),
        description="Daily loop: persist CoinGecko community_data to asset_community_history.",
    )
    community_snapshot_job_interval_seconds: int = Field(
        default=86_400,
        ge=3600,
        le=604_800,
        validation_alias=AliasChoices("ATLAS_COMMUNITY_SNAPSHOT_JOB_INTERVAL_SECONDS"),
    )
    community_admission_gate_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("ATLAS_COMMUNITY_ADMISSION_GATE_ENABLED"),
        description="Filter polaris:universe:all via Reddit/Telegram community thresholds.",
    )

    # ─── PROMETHEUS WS (FBI-02) ──────────────────────────────────────
    JWT_SECRET: str = "secret"
    JWT_ALGORITHM: str = "HS256"
    PORTFOLIO_PUSH_INTERVAL_S: float = 5.0

    def resolved_hydra_redis_url(self) -> str:
        """Redis URL for HYDRA ingestion (streams + hydra:*).

        Whitespace-only or empty ``HYDRA_REDIS_URL`` falls back to ``redis_url``.
        """
        trimmed = (self.hydra_redis_url or "").strip()
        if trimmed:
            return trimmed
        return self.redis_url

