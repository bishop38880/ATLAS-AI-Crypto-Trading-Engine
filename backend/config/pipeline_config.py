"""POLARIS pipeline configuration — gate thresholds, review timing, model endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field

from atlas.scoring.scoring_weights import (
    MODERATE_MIN,
    STRONG_MIN,
    WEAK_MIN,
    get_leverage_reference,
    get_position_size_pct,
)

LM_STUDIO_TIMEOUT_S: float = 30.0
DEEPSEEK_REVIEW_TIMEOUT_S: float = 28.0
MISTRAL_REVIEW_TIMEOUT_S: float = 25.0
MISTRAL_CHAT_BASE_URL: str = "https://api.mistral.ai/v1"

REVIEW_PARTIAL_EXIT_AT_S: float = 30.0
REVIEW_FULL_EXIT_AT_S: float = 60.0
PARTIAL_EXIT_PCT: float = 0.50

TIMEFRAME_CONFLUENCE_BONUS: int = 15
TIMEFRAME_CONFLICT_PENALTY: int = -10

TIER2_ESCALATION_SCORE_THRESHOLD: int = 110
TIER2_ESCALATION_TTL_SECONDS: int = 14400
TIER2_ASSETS: frozenset[str] = frozenset({"ZEC", "HYPE", "PYTH"})

CORE_ASSETS: frozenset[str] = frozenset({
    "BTC", "ETH", "SOL", "JUP", "LINK", "AVAX", "XRP", "NEAR",
})

PROMETHEUS_EXECUTION_CHANNEL: str = "polaris:prometheus:execution_requests"
PROMETHEUS_STAGED_EXIT_CHANNEL: str = "polaris:prometheus:staged_exit"
FILL_KEY_PREFIX: str = "polaris:fills:"


class GateConfig(BaseModel, frozen=True):
    """Per-timeframe gate thresholds."""

    min_score: int = Field(description="Minimum confluence score to pass this gate")
    min_direction_confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Minimum directional bias clarity",
    )
    requires_trend: bool = Field(
        description="When true, RANGING regime fails the gate",
    )
    description: str = Field(description="Human-readable gate purpose")


GATE_CONFIGS: dict[str, GateConfig] = {
    "daily": GateConfig(
        min_score=60,
        min_direction_confidence=0.65,
        requires_trend=True,
        description="Daily regime gate — confirms macro trend direction",
    ),
    "4hr": GateConfig(
        min_score=50,
        min_direction_confidence=0.60,
        requires_trend=False,
        description="4hr confirmation — must agree with daily direction",
    ),
    "1hr": GateConfig(
        min_score=120,
        min_direction_confidence=0.55,
        requires_trend=False,
        description="1hr signal gate — primary scoring threshold",
    ),
    "15min": GateConfig(
        min_score=110,
        min_direction_confidence=0.50,
        requires_trend=False,
        description="15min timing gate — entry momentum confirmation",
    ),
    "5min": GateConfig(
        min_score=100,
        min_direction_confidence=0.50,
        requires_trend=False,
        description="5min microstructure gate — final execution check",
    ),
}

TIMEFRAME_ORDER: tuple[str, ...] = ("daily", "4hr", "1hr", "15min", "5min")

TF_TO_INTERVAL: dict[str, str] = {
    "daily": "1D",
    "4hr": "4H",
    "1hr": "1H",
    "15min": "15",
    "5min": "5",
}


def score_cache_key(symbol: str, timeframe: str) -> str:
    """Redis key populated by the scoring scheduler."""
    return "score:{}:{}".format(symbol.lower(), timeframe)


def get_position_tier(score: int) -> dict[str, float | int] | None:
    """Map raw 220-point score to size % and leverage tier."""
    if score < WEAK_MIN:
        return None
    return {
        "min_score": (
            STRONG_MIN if score >= STRONG_MIN
            else MODERATE_MIN if score >= MODERATE_MIN
            else WEAK_MIN
        ),
        "size_pct": get_position_size_pct(score),
        "leverage": get_leverage_reference(score),
    }
