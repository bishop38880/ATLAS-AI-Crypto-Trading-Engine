"""SignalOutput — canonical signal schema for ATLAS → PROMETHEUS.

This is the core data model that every downstream component depends on.
PROMETHEUS subscribes to ``polaris:signals:{asset}`` and expects exactly
this structure. No field may be added or removed without updating the
POLARIS Context Document v3.0.

Session 00 hard wall:
    ``ActionBlock`` does NOT contain ``amount``. Position sizing is
    PROMETHEUS's exclusive responsibility — it requires portfolio equity,
    correlation-grade risk limit, and leverage, none of which ATLAS has
    visibility into.

IM-1 extensions:
    ``SubSignalResult`` — granular agent-output data point for the
    Intelligence Matrix pipeline.
    ``DeepSeekDecision`` — structured LLM output schema.
    Calibration fields (``calibrated_probability``, ``calibration_ece``)
    as Session 17 hooks.
    ``deepseek_evaluation``, ``agent_breakdown``, ``raw_confluence_score``
    for the full intelligence matrix signal.

Serialization:
    Always use ``msgspec.json.encode(signal.model_dump())``.
    Never ``model_dump_json()``, never stdlib ``json``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from pydantic import (BaseModel, ConfigDict, Field, field_validator,
                      model_validator)

from atlas.models.enums import CrossCorrelationGrade
from atlas.models.telemetry import TelemetryEvent

class SignalDirection(str, Enum):
    """Agent-level directional conviction."""
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"

class AgentTier(str, Enum):
    ANALYST = "analyst"      # Tier 1 safety gating
    RISK = "risk"            # Tier 2
    SYNTHESISER = "synthesiser"  # Tier 3

class AgentState(str, Enum):
    """Agent lifecycle state."""
    WARMING_UP = "WARMING_UP"
    READY = "READY"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"

class AgentCategory(str, Enum):
    TECHNICAL = "technical"
    DERIVATIVES = "derivatives"
    ONCHAIN = "onchain"
    SENTIMENT = "sentiment"
    RISK = "risk"
    CONTEXT = "context"
    FUNDING = "funding"
    NEWS_MACRO = "news_macro"
    WHALE = "whale"
    LIQUIDATION = "liquidation"
    REGIME = "regime"
    CORRELATION = "correlation"

class AgentTelemetry(BaseModel):
    """Telemetry data for agent execution.

    Includes latency tracking and optional agent-output metadata.
    """

    model_config = ConfigDict(frozen=True)

    latency_ms: float = 0.0
    value: Any = None
    metadata: dict[str, Any] = Field(default_factory=dict)

class AgentResult(BaseModel):
    """Immutable result from a single intelligence agent."""
    model_config = ConfigDict(frozen=True)
    agent_name: str = Field(min_length=1)
    score: int = Field(ge=0, le=220)
    max_score: int = Field(ge=0)
    weight: float = Field(default=1.0, ge=0.0, le=1.0)
    direction: SignalDirection = Field(default=SignalDirection.NEUTRAL)
    explanation: str = Field(default="")
    convergences: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    veto: bool = Field(default=False)
    sub_signals: dict[str, Any] = Field(default_factory=dict)
    telemetry: AgentTelemetry = Field(default_factory=AgentTelemetry)


class SignalDecision(str, Enum):
    """The six permitted decision values — no other values allowed.

    PROMETHEUS pattern-matches on these exact strings. Adding a seventh
    value requires a coordinated update across ATLAS, PROMETHEUS, and
    the POLARIS Dashboard.
    """

    STRONG_BUY = "Strong Buy"
    BUY = "Buy"
    HOLD = "Hold"
    SELL = "Sell"
    STRONG_SELL = "Strong Sell"
    NO_POSITION = "No Position"


class ExitReason(str, Enum):
    """Reason for position closure."""
    TAKE_PROFIT = "TAKE_PROFIT"
    STOP_LOSS = "STOP_LOSS"
    TRAILING_STOP = "TRAILING_STOP"
    TIME_EXPIRY = "TIME_EXPIRY"
    MANUAL = "MANUAL"
    LIQUIDATION = "LIQUIDATION"
    RISK_VETO = "RISK_VETO"

class MarketOutcome(str, Enum):
    """Categorical outcome based on PnL."""
    WIN = "WIN"
    LOSS = "LOSS"
    SCRATCH = "SCRATCH"

class OutcomeClassification(str, Enum):
    """Intelligence classification."""
    GOOD_WIN = "GOOD_WIN"
    LUCKY_WIN = "LUCKY_WIN"
    GOOD_LOSS = "GOOD_LOSS"
    BAD_LOSS = "BAD_LOSS"

class TradeOutcome(BaseModel):
    """Trade outcome payload from PROMETHEUS."""
    model_config = ConfigDict(frozen=True)
    signal_id: str = Field(min_length=1)
    pnl_pct: Decimal
    exit_reason: ExitReason

    @property
    def market_outcome(self) -> MarketOutcome:
        if self.pnl_pct >= Decimal("0.5"): return MarketOutcome.WIN
        if self.pnl_pct <= Decimal("-0.5"): return MarketOutcome.LOSS
        return MarketOutcome.SCRATCH

class ActionBlock(BaseModel):
    """Trade parameters — only populated for Buy/Sell decisions.

    ATLAS suggests price levels and direction. PROMETHEUS decides whether
    to execute, and is the exclusive owner of position sizing. All price
    fields use Decimal for precision.

    NOTE: ``amount`` is deliberately absent. Position sizing is PROMETHEUS's
    responsibility — it requires portfolio equity, correlation-grade risk
    limit, and leverage, none of which ATLAS has visibility into.

    Attributes:
        side: Trade direction, exactly "buy" or "sell".
        order_type: Order type, exactly "limit" or "market".
        price: Suggested entry price (Decimal, must be > 0).
        stop_loss: Suggested stop loss level (Decimal, must be > 0).
        take_profit: Suggested take profit level (Decimal, must be > 0).
    """

    model_config = ConfigDict(frozen=True)

    side: str = Field(pattern="^(buy|sell)$")
    order_type: str = Field(
        default="limit",
        pattern="^(limit|market)$",
    )
    price: Decimal = Field(
        gt=0,
        description="Suggested entry price",
    )
    stop_loss: Decimal = Field(
        gt=0,
        description="Suggested stop loss level",
    )
    take_profit: Decimal = Field(
        gt=0,
        description="Suggested take profit level",
    )


class CategoryScores(BaseModel, frozen=True):
    """Per-category score breakdown from the 220-point confluence scorer.

    Each field represents one agent category's contribution to the
    normalised 0–100 score. The ``total`` field is the sum of all
    individual category scores.

    Attributes:
        technical: Score contribution from TechnicalAgent.
        derivatives: Score contribution from DerivativesAgent.
        onchain: Score contribution from OnChainIntelligenceAgent.
        sentiment: Score contribution from SentimentAgent.
        whale: Score contribution from WhaleAgent.
        liquidation: Score contribution from LiquidationAgent.
        regime: Score contribution from RegimeAgent.
        funding: Score contribution from FundingAgent.
        news_macro: Score contribution from NewsMacroAgent.
        correlation: Score contribution from CorrelationAgent.
        macro: Score contribution from MacroCrossMarketAgent (liquidity/dominance).
        context: Score contribution from ContextAgent.
        total: Sum of all category scores.
    """

    technical: int = Field(ge=0, default=0)
    derivatives: int = Field(ge=0, default=0)
    onchain: int = Field(ge=0, default=0)
    sentiment: int = Field(ge=0, default=0)
    whale: int = Field(ge=0, default=0)
    liquidation: int = Field(ge=0, default=0)
    regime: int = Field(ge=0, default=0)
    funding: int = Field(ge=0, default=0)
    news_macro: int = Field(ge=0, default=0)
    correlation: int = Field(ge=0, default=0)
    macro: int = Field(ge=0, default=0)
    context: int = Field(ge=0, default=0)
    total: int = Field(ge=0, default=0)

    @model_validator(mode="after")
    def validate_total(self) -> "CategoryScores":
        """Ensure total matches the sum of individual category scores."""
        calculated = (
            self.technical + self.derivatives + self.onchain +
            self.sentiment + self.whale + self.liquidation +
            self.regime + self.funding + self.news_macro +
            self.correlation + self.macro + self.context
        )
        if self.total != calculated:
            raise ValueError(f"total ({self.total}) does not match sum of categories ({calculated})")
        return self


class ConfidenceDimensions(BaseModel, frozen=True):
    """Individual dimension scores that compose pipeline_confidence.

    All values in [0.0, 1.0]. Stored for post-trade analysis and
    to enable per-dimension diagnostics without re-running the pipeline.
    """

    conviction_tightness: Decimal = Field(
        default=Decimal("0.0"),
        ge=Decimal("0.0"),
        le=Decimal("1.0"),
        description="conviction_lower / conviction point estimate",
    )
    agent_coverage: Decimal = Field(
        default=Decimal("0.0"),
        ge=Decimal("0.0"),
        le=Decimal("1.0"),
        description="live agents / total agents dispatched",
    )
    gate_cleanliness: Decimal = Field(
        default=Decimal("0.0"),
        ge=Decimal("0.0"),
        le=Decimal("1.0"),
        description="1.0 - normalized flag count",
    )
    regime_stability: Decimal = Field(
        default=Decimal("0.0"),
        ge=Decimal("0.0"),
        le=Decimal("1.0"),
        description="1.0 - HMM transition_probability",
    )
    provider_agreement: Decimal = Field(
        default=Decimal("0.0"),
        ge=Decimal("0.0"),
        le=Decimal("1.0"),
        description="1.0 - normalized cross-source divergence",
    )


class ConfidenceTier(str, Enum):
    """Pipeline confidence tier determining signal treatment.

    STANDARD: pipeline_confidence >= 0.65 — full position_size_modifier.
    REDUCED:  0.40 <= pipeline_confidence < 0.65 — 0.50x modifier.
    SKIP:     pipeline_confidence < 0.40 — signal discarded.
    """

    STANDARD = "STANDARD"
    REDUCED = "REDUCED"
    SKIP = "SKIP"


# ---------------------------------------------------------------------------
# SubSignalResult — IM-1 Task 2
# ---------------------------------------------------------------------------


class SubSignalResult(BaseModel, frozen=True):
    """Granular data point from an agent — feeds the LLM correlation matrix.

    Each sub-signal represents a single metric or indicator value that
    the agent extracted during its analysis. The DeepSeek orchestrator
    consumes these to build the cross-agent correlation matrix.

    Attributes:
        value: Human-readable value (e.g. '+2.8 SD').
        flag: Uppercase categorization (e.g. 'EXTREME_SHORT_CROWDING').
        metadata: Optional key-value pairs for additional context.
    """

    value: str = Field(
        description="Human-readable value (e.g., '+2.8 SD')",
    )
    flag: str = Field(
        description="Uppercase categorization (e.g., 'EXTREME_SHORT_CROWDING')",
    )
    metadata: dict[str, str | int | float | bool] = Field(
        default_factory=dict,
    )

    @field_validator("flag")
    @classmethod
    def _flag_uppercase(cls, v: str) -> str:
        """Enforce uppercase-only flag values.

        Args:
            v: The flag value to validate.

        Returns:
            The validated flag string.

        Raises:
            ValueError: If flag contains lowercase characters.
        """
        if not v.isupper():
            raise ValueError("flag must be uppercase")
        return v


# ---------------------------------------------------------------------------
# DeepSeekDecision — IM-1 Task 3
# ---------------------------------------------------------------------------


class DeepSeekDecision(BaseModel, frozen=True):
    """Strict JSON schema DeepSeek MUST adhere to.

    Note on enum coercion: Pydantic v2 will coerce valid strings to enums
    during instantiation by default. If a wire payload arrives with
    cross_correlation_grade="STANDARD" (string), Pydantic will accept it
    and coerce to CrossCorrelationGrade.STANDARD. This is intentional —
    the wire format is strings; the in-memory domain model is enums.

    If you need to reject string coercion (e.g. to catch caller bugs in
    internal code paths that should pass enum members), set
    ``model_config = ConfigDict(strict=True)`` — not ``frozen=True``.
    ``frozen=True`` only prevents post-instantiation mutation.

    Attributes:
        decision: One of six permitted signal decisions.
        confidence: Model confidence (0.0–1.0).
        cross_correlation_grade: Correlation tier for PROMETHEUS risk.
        key_convergences: Cross-agent correlations identified.
        key_risks: Cross-agent contradictions / risks.
        reasoning: 2–3 sentence synthesis (max 1200 chars).
        would_change_if: Single most important reversal condition.
    """

    decision: SignalDecision
    confidence: Decimal = Field(ge=Decimal("0.0"), le=Decimal("1.0"))
    cross_correlation_grade: CrossCorrelationGrade
    key_convergences: list[str] = Field(
        description="Cross-agent correlations identified",
    )
    key_risks: list[str] = Field(
        description="Cross-agent contradictions / risks",
    )
    reasoning: str = Field(
        description="2-3 sentence synthesis",
        max_length=1200,
    )
    would_change_if: str = Field(
        description="Single most important reversal condition",
    )


# ---------------------------------------------------------------------------
# Actionable decision set — used by validators
# ---------------------------------------------------------------------------

_ACTIONABLE_DECISIONS: frozenset[SignalDecision] = frozenset(
    {
        SignalDecision.BUY,
        SignalDecision.STRONG_BUY,
        SignalDecision.SELL,
        SignalDecision.STRONG_SELL,
    }
)


# ---------------------------------------------------------------------------
# Schema version — ATLAS↔PROMETHEUS contract version
# ---------------------------------------------------------------------------

SIGNAL_SCHEMA_VERSION: str = "2.0.0"
"""Current signal schema version.

PROMETHEUS must reject signals with an unknown schema_version.
Bump this when adding/removing/renaming fields on SignalOutput.
"""

# ---------------------------------------------------------------------------
# SignalOutput — Session 00 base + IM-1 extensions
# ---------------------------------------------------------------------------


class SignalOutput(BaseModel):
    """Complete signal published to ``polaris:signals:{asset}``.

    This is the canonical contract between ATLAS and PROMETHEUS. Every
    field here is documented in the POLARIS Context Document v3.0,
    Section 5 — Signal Output Schema.

    Attributes:
        signal_id: Canonical correlation key (UUID4).
        timestamp: UTC time of signal generation.
        decision: One of six permitted values.
        asset: Trading pair (e.g. "BTCUSDT").
        timeframe: Primary decision timeframe (default "30m").
        action: Trade parameters (None for Hold/No Position).
        expires_at: UTC deadline after which PROMETHEUS discards.
        reasoning_summary: 2–3 sentence synthesis.
        key_convergences: Strongest convergence factors.
        key_risks: Identified risk factors.
        is_cascade_triggered: True during active HYDRA cascade.
        hydra_event_id: Links to HYDRA cascade matrix ID.
        score: Normalised confluence score (0–100).
        confidence: Agent confidence (0.0–1.0).
        category_scores: Per-category score breakdown.
        contributing_graph_paths: GNN graph paths (future).
        telemetry: Pipeline cycle telemetry.
        calibrated_probability: Isotonic-calibrated probability (Session 17).
        calibration_ece: Expected calibration error (Session 17).
        deepseek_evaluation: Structured LLM output (IM-1).
        agent_breakdown: Per-agent scoring with sub-signal matrices.
        raw_confluence_score: Raw 220-point score before normalisation.
    """

    model_config = ConfigDict(frozen=True)

    # --- CONTRACT VERSION ---
    schema_version: str = Field(
        default=SIGNAL_SCHEMA_VERSION,
        description=(
            "ATLAS↔PROMETHEUS contract version. PROMETHEUS must reject "
            "signals with schema_version not in its allowed-versions set. "
            "Bump when adding/removing/renaming fields."
        ),
    )

    # --- IDENTITY ---
    signal_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description=(
            "Canonical correlation key. PROMETHEUS records this on order "
            "placement and passes it back verbatim in TradeOutcome.signal_id. "
            "Never synthesise from timestamp+asset — that breaks cross-system "
            "matching."
        ),
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    decision: SignalDecision = Field(
        description="One of six permitted values",
    )
    asset: str = Field(
        min_length=1,
        description="e.g. BTCUSDT",
    )
    timeframe: str = Field(
        default="30m",
        description="Primary decision timeframe",
    )

    # --- ACTION ---
    action: ActionBlock | None = Field(
        default=None,
        description=(
            "Populated for Buy/Sell decisions only. " "None for Hold/No Position."
        ),
    )
    expires_at: datetime = Field(
        description=("UTC timestamp after which PROMETHEUS must discard this signal"),
    )

    # --- REASONING ---
    reasoning_summary: str = Field(
        default="",
        description="2-3 sentence synthesis",
    )
    key_convergences: list[str] = Field(default_factory=list)
    key_risks: list[str] = Field(default_factory=list)

    # --- HYDRA CASCADE LINKAGE ---
    is_cascade_triggered: bool = Field(
        default=False,
        description=(
            "True if this signal was generated during an active " "HYDRA cascade"
        ),
    )
    hydra_event_id: str | None = Field(
        default=None,
        description=(
            "Links to the specific liquidation cascade matrix ID " "from HYDRA"
        ),
    )

    # --- SCORING ---
    score: int = Field(
        ge=0,
        le=100,
        description=(
            "Normalised confluence score (0-100). Raw 220-point score "
            "lives in raw_confluence_score on the intelligence matrix "
            "extension — Session IM-1."
        ),
    )
    confidence: Decimal = Field(ge=Decimal("0.0"), le=Decimal("1.0"))
    category_scores: CategoryScores
    contributing_graph_paths: list[str] = Field(default_factory=list)
    telemetry: TelemetryEvent

    # === CALIBRATION (Session 17 hook) ===
    calibrated_probability: Decimal | None = Field(
        default=None,
        ge=Decimal("0.0"),
        le=Decimal("1.0"),
        description=(
            "Isotonic-calibrated probability of profitable outcome. "
            "None until Session 17 lands."
        ),
    )
    calibration_ece: float | None = Field(
        default=None,
        ge=0.0,
        description=(
            "Expected calibration error from current isotonic model. "
            "None until Session 17 lands."
        ),
    )

    # === INTELLIGENCE MATRIX (IM-1) ===
    deepseek_evaluation: DeepSeekDecision | None = Field(
        default=None,
        description=(
            "Populated for high-conviction signals (raw score >= 140). "
            "None for routine signals where DeepSeek is not invoked."
        ),
    )
    agent_breakdown: dict[str, AgentResult] = Field(
        default_factory=dict,
        description=("Per-agent scoring contributions with sub-signal matrices."),
    )
    raw_confluence_score: int = Field(
        default=0,
        ge=0,
        le=220,
        description=(
            "Raw 220-point confluence score before normalisation. "
            "The normalised 0-100 form is in `score` (Session 00)."
        ),
    )

    # === DERIVED CONFIDENCE GATE (Session 20) ===
    pipeline_confidence: Decimal = Field(
        default=Decimal("0.0"),
        ge=Decimal("0.0"),
        le=Decimal("1.0"),
        description="Deterministic confidence score derived from pipeline signals",
    )
    confidence_dimensions: ConfidenceDimensions | None = Field(
        default=None,
        description="Per-dimension breakdown for post-trade diagnostics",
    )
    confidence_tier: ConfidenceTier = Field(
        default=ConfidenceTier.STANDARD,
        description="Signal treatment tier based on pipeline_confidence",
    )
    position_size_modifier: Decimal = Field(
        default=Decimal("1.0"),
        ge=Decimal("0.0"),
        le=Decimal("1.0"),
        description="Applied by orchestrator: 0.0 (SKIP), 0.5 (REDUCED), 1.0 (STANDARD)",
    )
    human_review_flag: bool = Field(
        default=False,
        description="True only when conviction >= 140 AND confidence < 0.50",
    )

    # === CQR / UNCERTAINTY BOUNDS (Phase 1 — additive metadata, 0–220 scale) ===
    conviction_lower: int | None = Field(
        default=None,
        ge=0,
        le=220,
        description=(
            "Optional lower bound on raw confluence conviction (same scale as "
            "raw_confluence_score). None when CQR disabled or calibration "
            "insufficient."
        ),
    )
    conviction_upper: int | None = Field(
        default=None,
        ge=0,
        le=220,
        description=(
            "Optional upper bound on raw confluence conviction (same scale as "
            "raw_confluence_score)."
        ),
    )
    bounds_method: str | None = Field(
        default=None,
        description="How bounds were produced (e.g. RULE_PHASE1, CQR_DISABLED).",
    )
    bounds_width: int | None = Field(
        default=None,
        ge=0,
        le=220,
        description="conviction_upper - conviction_lower when bounds are present.",
    )

    # --- VALIDATORS ---

    @field_validator("signal_id")
    @classmethod
    def _signal_id_non_empty(cls, v: str) -> str:
        """Reject empty or whitespace-only signal IDs.

        Args:
            v: The signal_id value to validate.

        Returns:
            The validated signal_id.

        Raises:
            ValueError: If signal_id is empty or whitespace.
        """
        if not v or not v.strip():
            raise ValueError("signal_id must be non-empty")
        return v

    @model_validator(mode="after")
    def _action_matches_decision(self) -> SignalOutput:
        """Enforce action/decision consistency and TTL validity.

        Rules:
            - Actionable decisions require a non-None ActionBlock.
            - Non-actionable decisions must have action=None.
            - expires_at must be strictly after timestamp.
            - cascade_triggered=True requires a hydra_event_id.

        Returns:
            Self if all rules pass.

        Raises:
            ValueError: On any rule violation.
        """
        _validate_action_decision_consistency(
            self.decision,
            self.action,
        )
        _validate_expiry_after_timestamp(
            self.expires_at,
            self.timestamp,
        )
        _validate_cascade_event_linkage(
            self.is_cascade_triggered,
            self.hydra_event_id,
        )
        _validate_conviction_bounds_metadata(self)
        return self


# ---------------------------------------------------------------------------
# Extracted validator helpers (keep model_validator under 40 lines)
# ---------------------------------------------------------------------------


def _validate_action_decision_consistency(
    decision: SignalDecision,
    action: ActionBlock | None,
) -> None:
    """Check that actionable decisions have an ActionBlock and vice versa.

    Args:
        decision: The signal decision.
        action: The action block (may be None).

    Raises:
        ValueError: If consistency is violated.
    """
    if decision in _ACTIONABLE_DECISIONS and action is None:
        raise ValueError(f"decision={decision.value} requires an ActionBlock")
    if decision not in _ACTIONABLE_DECISIONS and action is not None:
        raise ValueError(f"decision={decision.value} must have action=None")


def _validate_expiry_after_timestamp(
    expires_at: datetime,
    timestamp: datetime,
) -> None:
    """Check that expires_at is strictly after timestamp.

    Args:
        expires_at: Signal expiration time.
        timestamp: Signal creation time.

    Raises:
        ValueError: If expires_at <= timestamp.
    """
    if expires_at <= timestamp:
        raise ValueError("expires_at must be strictly after timestamp")


def _validate_conviction_bounds_metadata(sig: SignalOutput) -> None:
    """Ensure optional CQR fields are either all unset or internally consistent."""
    lo, hi = sig.conviction_lower, sig.conviction_upper
    method, width = sig.bounds_method, sig.bounds_width
    present = (lo is not None, hi is not None, method is not None, width is not None)
    if any(present) and not all(present):
        raise ValueError(
            "conviction_lower, conviction_upper, bounds_method, and bounds_width "
            "must all be set together, or all left as None",
        )
    if lo is not None and hi is not None and width != hi - lo:
        raise ValueError("bounds_width must equal conviction_upper - conviction_lower")


def _validate_cascade_event_linkage(
    is_cascade_triggered: bool,
    hydra_event_id: str | None,
) -> None:
    """Check that cascade-triggered signals carry an event ID.

    Args:
        is_cascade_triggered: Whether a cascade was active.
        hydra_event_id: The HYDRA event ID (may be None).

    Raises:
        ValueError: If cascade is triggered without an event ID.
    """
    if is_cascade_triggered and hydra_event_id is None:
        raise ValueError("is_cascade_triggered=True requires hydra_event_id")


# ---------------------------------------------------------------------------
# Fallback constructor — IM-1 Task 5
# ---------------------------------------------------------------------------


def build_safe_fallback_decision(reason: str) -> DeepSeekDecision:
    """Neutral decision when DeepSeek evaluation fails.

    Used by deepseek_client.py on API failure. Returns HOLD / STANDARD
    so PROMETHEUS applies minimum risk limit — never amplifies on failure.

    Passes the enum member (not a bare string) for hygiene and to make
    the invariant explicit at the call site.

    Args:
        reason: Human-readable description of the failure.

    Returns:
        A DeepSeekDecision with HOLD decision and STANDARD grade.
    """
    return DeepSeekDecision(
        decision=SignalDecision.HOLD,
        confidence=Decimal("0.0"),
        cross_correlation_grade=CrossCorrelationGrade.STANDARD,
        key_convergences=["API_FAILURE_FALLBACK"],
        key_risks=["API_FAILURE_FALLBACK"],
        reasoning="Fallback decision: {}".format(reason),
        would_change_if="Upstream evaluation path recovers.",
    )


# ---------------------------------------------------------------------------
# Forward-reference resolution for AgentResult
# ---------------------------------------------------------------------------


def _rebuild_forward_refs() -> None:
    """Resolve forward references for both AgentResult and SignalOutput.

    Rebuilds in dependency order:
    1. AgentResult first — resolves SubSignalResult forward ref
    2. SignalOutput second — resolves AgentResult forward ref

    This function is the single point of forward-reference resolution
    for the circular dependency between signal.py and agents/base.py.
    agents/base.py does NOT call model_rebuild itself.
    """
    AgentResult.model_rebuild(
        _types_namespace={"SubSignalResult": SubSignalResult},
    )
    SignalOutput.model_rebuild(
        _types_namespace={"AgentResult": AgentResult},
    )


_rebuild_forward_refs()
