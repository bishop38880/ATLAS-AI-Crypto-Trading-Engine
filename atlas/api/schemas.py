from typing import Annotated, Any, Literal
from decimal import Decimal, InvalidOperation
from pydantic import BaseModel, ConfigDict, AfterValidator, Field, Field
from pydantic.alias_generators import to_camel

def _validate_decimal_string(v: str) -> str:
    try:
        Decimal(v)
    except InvalidOperation as e:
        raise ValueError(f"not a valid Decimal string: {v!r}") from e
    return v

StringDecimal = Annotated[str, AfterValidator(_validate_decimal_string)]

_BaseConfig = ConfigDict(
    frozen=True,
    populate_by_name=True,
    alias_generator=to_camel,
    extra="forbid",
)

class AgentStatusRedis(BaseModel):
    model_config = _BaseConfig
    status: Literal["GREEN", "YELLOW", "RED", "DEGRADED", "FAILED", "WARMING_UP", "READY"]
    last_ping_ms: int
    last_score: float | None = None
    max_points: float | None = None
    direction: str | None = None
    explanation: str | None = None

class AgentStatusPayload(BaseModel):
    model_config = _BaseConfig
    name: str
    status: Literal["GREEN", "YELLOW", "RED"]
    last_ping_ms: int
    category: str  # canonical from AGENT_CATEGORIES; "unknown" if missing
    last_score: float | None = None
    max_points: float | None = None
    direction: str | None = None
    explanation: str | None = None

class ScoresPayload(BaseModel):
    model_config = _BaseConfig
    total_score: int
    normalized_score: int = 0
    category_scores: dict[str, int]
    confidence: float
    gate_threshold: int = 0
    passes_gate: bool
    is_stale: bool
    timestamp: str  # ISO 8601 UTC
    asset: str = ""
    decision: str = ""
    reasoning_summary: str = ""
    llm_tier_label: str = ""
    cycle_number: int | None = None
    cycle_timestamp: str = ""
    price: str = "0"
    funding_rate: str = "0"
    open_interest: str = "0"
    obti_summary: str | None = None
    obti_side: str | None = None

class PricePayload(BaseModel):
    model_config = _BaseConfig
    symbol: str
    price: StringDecimal
    change_24h: float  # dimensionless ratio — float OK
    volume_24h: StringDecimal
    timestamp: str

class SignalHistoryEntry(BaseModel):
    model_config = _BaseConfig
    id: int
    asset: str
    timestamp: str
    total_score: float
    decision: Literal["LONG", "SHORT", "FLAT", "REJECT", "Strong Buy", "Buy", "Hold", "Sell", "Strong Sell", "No Position"]
    conviction: float
    passes_gate: bool

class SignalFeedEntry(BaseModel):
    model_config = _BaseConfig
    asset: str
    timestamp: str
    decision: str
    total_score: float
    normalized_score: float
    confidence: float
    passes_gate: bool
    gate_threshold: float | None
    obti_summary: str | None
    obti_side: str | None
    llm_tier_label: str | None
    cycle_number: int | None
    price: str
    change_24h: str

class OBTIDetail(BaseModel):
    model_config = _BaseConfig
    side: str | None
    level: str
    obti_bid: float
    obti_ask: float
    moderate_threshold: float
    extreme_threshold: float
    samples: int
    min_samples: int
    is_warming_up: bool
    history: list[float]
    last_book_update_ms: int

class FracDiffDetail(BaseModel):
    model_config = _BaseConfig
    optimal_order: float
    memory_preserved: float
    adf_p_value: float
    is_stationary: bool
    last_calibrated: str

class OptionsFlowDetail(BaseModel):
    model_config = _BaseConfig
    provider: dict[str, str] | None
    dvol: float | None
    atm_iv: float | None
    skew25d: float | None
    vrp: float | None
    call_put_oi_ratio: str | None
    regime: str | None
    unusual_activity: str | None

class ExchangeFlowDetail(BaseModel):
    model_config = _BaseConfig
    provider: dict[str, str] | None
    net_flow_24h: str | None
    flow_z_score: float | None
    flow_trend: str | None
    stablecoin_reserves: str | None
    signal: str | None

class AgentBreakdownCellPublic(BaseModel):
    """Public slice of ``AgentResult`` for the asset inspector scorecard."""

    model_config = _BaseConfig

    score: float = Field(default=0.0, ge=0.0)
    max_score: float = Field(default=0.0, ge=0.0)
    weight: float = Field(default=1.0, ge=0.0)
    direction: str | None = None
    explanation: str = ""
    convergences: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    veto: bool = False
    sub_signals: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Sub-signal key → value map (structure agent-specific).",
    )


class SignalDetailPayload(BaseModel):
    model_config = _BaseConfig
    asset: str
    price: str
    change_24h: str
    cycle_number: int
    cycle_ts: str
    total_score: float
    normalized_score: float
    decision: str
    confidence: float
    passes_gate: bool
    gate_threshold: float
    llm_tier_label: str | None
    category_scores: dict[str, float]
    category_max_scores: dict[str, float]
    agent_breakdown: dict[str, AgentBreakdownCellPublic] = Field(default_factory=dict)
    obti_detail: OBTIDetail | None
    frac_diff: FracDiffDetail | None
    options_flow: OptionsFlowDetail | None
    exchange_flow: ExchangeFlowDetail | None
    signal_history: list[SignalHistoryEntry]


class ConfluenceScoreHistoryPoint(BaseModel):
    """One observation for pillar / total score time series (RAG ``signal_history``)."""

    model_config = _BaseConfig

    timestamp: str
    raw_score: float
    normalized_score: float
    decision: str
    derivatives: float | None = None
    onchain: float | None = None
    technical: float | None = None
    sentiment: float | None = None
    market_context: float | None = None


class DerivativesFundingPoint(BaseModel):
    model_config = _BaseConfig

    funding_time_ms: int
    funding_rate: float


class DerivativesOiPoint(BaseModel):
    model_config = _BaseConfig

    ts_ms: int
    oi_usd: float


class RecentDecisionOutcome(BaseModel):
    model_config = _BaseConfig

    signal_id: str
    timestamp: str
    decision: str
    raw_score: float
    normalized_score: float
    outcome_label: str | None = None
    pnl_pct: float | None = None


class AssetInspectorPayload(BaseModel):
    """Bundled time series + outcomes for the executive asset inspector page."""

    model_config = _BaseConfig

    asset: str
    score_history: list[ConfluenceScoreHistoryPoint]
    funding_history: list[DerivativesFundingPoint]
    oi_history: list[DerivativesOiPoint]
    derivatives_source: Literal["okx_mcp_cache", "none"]
    recent_decisions: list[RecentDecisionOutcome]


class AgentVerdictJournal(BaseModel):
    model_config = _BaseConfig
    agent_name: str = Field(description="Agent identifier at scoring time")
    state: str = Field(description="Agent lifecycle state")
    score: int = Field(ge=0, description="Agent points on the confluence ladder")
    max_score: int = Field(ge=0, description="Ceiling for this agent")
    direction: str = Field(description="Directional label")
    veto: bool = Field(default=False, description="Whether the agent issued a veto")


class RiskManagerJournal(BaseModel):
    model_config = _BaseConfig
    passed: bool = Field(description="False when Risk Manager vetoed the trade idea")
    vetoed: bool = Field(description="True when veto blocked emission")
    reason: str = Field(description="Primary explanation from verdicts or metadata")


class GateEventJournal(BaseModel):
    model_config = _BaseConfig
    name: str
    fired: bool
    detail: str = ""


class OutcomeHorizonsPayload(BaseModel):
    model_config = _BaseConfig
    pct_1h: float | None = Field(description="Marked-to-market or realised move ~1h after signal")
    pct_4h: float | None = Field(description="~4h horizon outcome % when recorded")
    pct_24h: float | None = Field(description="~24h horizon — aligns with final pnl when back-filled")


class DecisionJournalEntry(BaseModel):
    model_config = _BaseConfig
    signal_id: str
    asset: str
    timestamp: str
    timeframe: str
    score_breakdown: str = Field(description="Human-readable score snapshot for the row")
    normalized_score: int = Field(ge=0, le=100)
    raw_score: int = Field(ge=0, le=220)
    decision: str
    confidence: float
    action_taken: str = Field(
        description="executed | skipped | rejected — derived from gates and PROMETHEUS path",
    )
    entry_price: str = Field(description="Suggested entry from metadata when present")
    outcome_pct: float | None = Field(description="Primary labelled outcome (final trade PnL %)")
    outcome_horizons: OutcomeHorizonsPayload
    reasoning_summary: str
    primary_agent: str | None = Field(description="Agent that vetoed or led score")
    agent_verdicts: list[AgentVerdictJournal]
    gates: list[GateEventJournal]
    risk_manager: RiskManagerJournal
    pipeline_confidence: float | None = None
    confidence_tier: str | None = None
    exit_reason: str | None = None


class DecisionJournalResponse(BaseModel):
    model_config = _BaseConfig
    entries: list[DecisionJournalEntry]
    total: int
    has_more: bool = False
    next_offset: int | None = None


class PaperEquityCurvePoint(BaseModel):
    """Single timestamped equity observation for heuristic paper validation."""

    model_config = _BaseConfig
    ts: str
    equity_usd: StringDecimal


class PaperDrawdownPoint(BaseModel):
    model_config = _BaseConfig
    ts: str
    drawdown_pct: float = Field(description="Percent drawdown from running peak equity")


class PaperTierWinBlock(BaseModel):
    model_config = _BaseConfig
    wins: int = Field(ge=0)
    trades: int = Field(ge=0)
    win_rate: float = Field(ge=0.0, le=1.0)


class PaperParallelValidationPayload(BaseModel):
    model_config = _BaseConfig
    initial_usd: StringDecimal
    ending_usd: StringDecimal
    equity_curve: list[PaperEquityCurvePoint]
    drawdown_curve: list[PaperDrawdownPoint]
    weekly_sharpe_annualized: float
    tier_win_rates: dict[str, PaperTierWinBlock]
    row_count_used: int = Field(ge=0)
    pricing_model: str = Field(description="Identifier for the synthetic path construction")
    disclaimer: str = Field(
        description="Human-readable guardrail on interpreting USD paths",
    )


class BacktestInventoryPayload(BaseModel):
    """DuckDB warehouse row counts for the dashboard backtest suite."""

    model_config = _BaseConfig
    db_path: str
    candle_count: int = Field(ge=0)
    signal_count: int = Field(ge=0)
    run_count: int = Field(ge=0)
    assets: list[str]


class BacktestRunSummaryItem(BaseModel):
    model_config = _BaseConfig
    run_id: str
    created_at_iso: str
    asset: str
    timeframe: str
    start_ts_iso: str
    end_ts_iso: str
    initial_capital_usd: StringDecimal
    net_pnl_usd: StringDecimal | None = None
    win_rate: float | None = None
    total_trades: int | None = None
    veto_count: int | None = None


class BacktestRunListPayload(BaseModel):
    model_config = _BaseConfig
    runs: list[BacktestRunSummaryItem]


class BacktestEquityPoint(BaseModel):
    model_config = _BaseConfig
    ts_iso: str
    equity_usd: StringDecimal


class BacktestTradeRow(BaseModel):
    model_config = _BaseConfig
    trade_id: str
    signal_id: str
    asset: str
    direction: str
    entry_ts_iso: str
    exit_ts_iso: str | None = None
    entry_price: StringDecimal
    exit_price: StringDecimal | None = None
    net_pnl_usd: StringDecimal | None = None
    exit_reason: str | None = None
    risk_veto: bool = False


class BacktestMetricsPayload(BaseModel):
    model_config = _BaseConfig
    run_id: str
    total_trades: int = Field(ge=0)
    winning_trades: int = Field(ge=0)
    losing_trades: int = Field(ge=0)
    win_rate: float
    gross_pnl_usd: StringDecimal
    total_fees_usd: StringDecimal
    net_pnl_usd: StringDecimal
    max_drawdown_usd: StringDecimal
    max_drawdown_pct: float
    sharpe_ratio: float | None = None
    sortino_ratio: float | None = None
    profit_factor: float | None = None
    avg_hold_seconds: float | None = None
    veto_count: int = Field(ge=0)


class BacktestRunDetailPayload(BaseModel):
    model_config = _BaseConfig
    run: BacktestRunSummaryItem
    metrics: BacktestMetricsPayload
    equity_curve: list[BacktestEquityPoint]
    trades: list[BacktestTradeRow]
    disclaimer: str


class BacktestRunRequestBody(BaseModel):
    model_config = _BaseConfig
    asset: str = Field(description="Perp symbol e.g. BTCUSDT")
    timeframe: str = Field(description="Candle timeframe matching warehouse rows e.g. 1h")
    start_day: str = Field(description="Inclusive UTC day YYYY-MM-DD")
    end_day: str = Field(description="Inclusive UTC day YYYY-MM-DD")
    initial_capital_usd: StringDecimal = Field(default="10000")
    score_threshold: float = Field(default=65.0, ge=0.0, le=220.0)


class BacktestRunCreatedPayload(BaseModel):
    model_config = _BaseConfig
    run_id: str
    metrics: BacktestMetricsPayload
    equity_curve: list[BacktestEquityPoint]
    disclaimer: str


class BacktestSeedPayload(BaseModel):
    model_config = _BaseConfig
    candles_imported: int = Field(ge=0)
    signals_imported: int = Field(ge=0)
    asset: str
    timeframe: str
    message: str


class RiskGovernorLimits(BaseModel):
    """Risk policy limits shown beside live telemetry (documentation / calibration)."""

    model_config = _BaseConfig
    total_exposure_max_pct: float = Field(description="Hard veto threshold for total gross exposure")
    daily_drawdown_limit_pct: float = Field(description="Rolling daily DD policy limit")
    weekly_drawdown_limit_pct: float = Field(description="Rolling weekly DD policy limit")
    intraday_pnl_1h_veto_pct: float = Field(
        serialization_alias="intradayPnl1hVetoPct",
        description="Risk agent fast-path 1h PnL veto line (fractional → percent)",
    )
    intraday_pnl_4h_veto_pct: float = Field(
        serialization_alias="intradayPnl4hVetoPct",
        description="4h PnL all-position veto line",
    )
    intraday_pnl_24h_shutdown_pct: float = Field(
        serialization_alias="intradayPnl24hShutdownPct",
        description="24h PnL kill-switch publish threshold",
    )


class TierExposureRow(BaseModel):
    model_config = _BaseConfig
    tier_id: str = Field(description="Bucket identifier: core | majors | l1_l2 | defi | rotation")
    label: str
    exposure_pct: float = Field(description="0–100 share of portfolio notionally allocated to this bucket")


class PipelineCircuitState(BaseModel):
    model_config = _BaseConfig
    stage: str
    state: str = Field(description="Redis cb:{stage}:state — closed | open | degraded | half_open | unknown")


class VetoHistoryDay(BaseModel):
    model_config = _BaseConfig
    day_iso: str
    veto_count: int = Field(ge=0)


class VetoHistoryBucket(BaseModel):
    model_config = _BaseConfig
    reason: str
    count: int = Field(ge=0)


class RecentRiskVeto(BaseModel):
    model_config = _BaseConfig
    ts_iso: str
    asset: str
    cycle_id: str
    reasons: list[str]


class RiskGovernorSnapshot(BaseModel):
    model_config = _BaseConfig
    as_of_iso: str
    total_portfolio_exposure_pct: float
    daily_drawdown_pct: float
    weekly_drawdown_pct: float
    trailing_pnl_1h_pct: float = Field(serialization_alias="trailingPnl1hPct")
    trailing_pnl_4h_pct: float = Field(serialization_alias="trailingPnl4hPct")
    trailing_pnl_24h_pct: float = Field(serialization_alias="trailingPnl24hPct")
    max_single_position_allowed_pct: float
    portfolio_equity_usd: str
    tier_exposure: list[TierExposureRow]
    pipeline_circuits: list[PipelineCircuitState]
    trading_halted: bool
    halt_reason: str | None = None
    halt_triggered_by: str | None = None
    halt_timestamp_iso: str | None = None
    policy_limits: RiskGovernorLimits
    veto_events_30d_total: int = Field(ge=0)
    veto_history_by_day: list[VetoHistoryDay]
    veto_reason_buckets: list[VetoHistoryBucket]
    recent_vetoes: list[RecentRiskVeto]
