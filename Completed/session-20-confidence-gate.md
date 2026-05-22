# SESSION 20 — Derived Pipeline Confidence Gate

## Context Files
@pipeline/scorer.py @atlas/shared/config.py @atlas/core/complexity_router.py
@atlas/ml/regime_detector.py @core/validation_gate.py @agents/base.py

---

## Goal

Add a deterministic, auditable `pipeline_confidence` score (0.0–1.0) to
`ConfluenceResult`. This score is derived entirely from observable pipeline
signals — not from LLM self-reporting. It gates how far a signal travels
through the pipeline and what position size modifier it receives.

**Design principle:** A self-reported LLM confidence score is unreliable.
LLMs are overconfident on patterns that resemble training data, and
underconfident on correct signals in novel regimes — the exact opposite
of what we need. Derived confidence from the pipeline's existing signals
is deterministic, backtestable, and can't be hallucinated.

**This is a non-breaking schema addition.** All existing consumers of
`ConfluenceResult` continue to work. The new fields have defaults.

---

## What pipeline_confidence measures

Five independent dimensions, each normalized to [0.0, 1.0], combined
into a single weighted score:

| Dimension | Weight | Source |
|---|---|---|
| Conviction interval tightness | 0.30 | `conviction_lower` / `conviction` (point estimate) |
| Agent data coverage | 0.25 | n agents with live data / total agents dispatched |
| Validation Gate cleanliness | 0.20 | anomaly flag count + consistency warning count |
| Regime stability | 0.15 | HMM `transition_probability` (inverted) |
| Provider agreement | 0.10 | cross-source consistency score from Validation Gate |

```
pipeline_confidence = (
    0.30 * conviction_tightness +
    0.25 * agent_coverage +
    0.20 * gate_cleanliness +
    0.15 * regime_stability +
    0.10 * provider_agreement
)
```

All inputs are already computed inside the pipeline on every cycle.
This adds zero new I/O and zero new latency.

---

## Gating logic (applied by Orchestrator, not by scorer)

```
pipeline_confidence < 0.40  → SKIP signal entirely
                               Log: CONFIDENCE_GATE_SKIP
                               Do NOT re-query. Do NOT escalate to human.
                               Log all five dimension scores for post-trade review.

0.40 ≤ pipeline_confidence < 0.65 → REDUCED signal
                               Forward to Grok/synthesis stage normally.
                               Set position_size_modifier = 0.50
                               Tag signal: confidence_tier = "REDUCED"
                               Flag for post-trade review (not pre-trade human review)

pipeline_confidence ≥ 0.65  → STANDARD signal
                               Standard pipeline, full position_size_modifier = 1.0
                               confidence_tier = "STANDARD"
```

**Exception — human review flag (pre-trade):**
Only trigger human review when BOTH conditions are true simultaneously:
- `conviction` ≥ 140 (neuro-symbolic threshold, already a high-stakes signal)
- `pipeline_confidence` < 0.50

This is the genuine paradox: high numeric conviction but low pipeline
confidence. Everything else either passes or gets skipped silently.

**RULE:** Never re-query the LLM on low confidence. A second call
rarely changes the confidence dimension scores (which are pipeline-level,
not model-level), costs additional API budget, and adds latency to the
analysis cycle. Skip and log.

---

## Task 1 — Schema additions

Update `ConfluenceResult` in `pipeline/scorer.py`:

```python
class ConfidenceDimensions(BaseModel, frozen=True):
    """Individual dimension scores that compose pipeline_confidence.

    All values in [0.0, 1.0]. Stored for post-trade analysis and
    to enable per-dimension diagnostics without re-running the pipeline.
    """
    conviction_tightness: float    # conviction_lower / conviction point estimate
    agent_coverage: float          # live agents / total agents dispatched
    gate_cleanliness: float        # 1.0 - normalized flag count
    regime_stability: float        # 1.0 - HMM transition_probability
    provider_agreement: float      # 1.0 - normalized cross-source divergence


class ConfidenceTier(str, Enum):
    STANDARD = "STANDARD"   # pipeline_confidence >= 0.65 — full position_size_modifier
    REDUCED  = "REDUCED"    # 0.40 <= pipeline_confidence < 0.65 — 0.50x modifier
    SKIP     = "SKIP"       # pipeline_confidence < 0.40 — signal discarded


class ConfluenceResult(BaseModel, frozen=True):
    # --- existing fields (unchanged) ---
    total_score: int
    breakdown: dict[str, CategoryResult]
    regime_multiplier: float
    sentiment_was_gated: bool
    signal_strength: str
    position_size_pct: float
    bias: str
    reasoning: str
    data_source_map: dict[str, str]
    shadow_metrics: ShadowMetrics
    version: str
    conviction_lower: int | None = None   # from CQR (Session 17)
    conviction_upper: int | None = None

    # --- v2.3: derived confidence gate ---
    pipeline_confidence: float = 0.0           # 0.0–1.0, deterministic
    confidence_dimensions: ConfidenceDimensions | None = None
    confidence_tier: ConfidenceTier = ConfidenceTier.STANDARD
    position_size_modifier: float = 1.0        # applied by orchestrator, 0.5 or 1.0
    human_review_flag: bool = False            # True only when conviction >= 140 AND confidence < 0.50
```

---

## Task 2 — PipelineConfidenceCalculator

Create `atlas/ml/confidence_gate.py`:

```python
from pydantic import BaseModel, Field
from atlas.pipeline.scorer import ConfidenceDimensions, ConfidenceTier


class PipelineConfidenceInputs(BaseModel, frozen=True):
    """All inputs needed to compute pipeline_confidence.

    Collected by the Orchestrator from existing pipeline outputs.
    Zero new I/O required — all fields are already computed.
    """
    # From ConfluenceResult (CQR — Session 17)
    conviction_point_estimate: int          # the raw `total_score`
    conviction_lower: int | None            # None if CQR not yet trained

    # From Orchestrator agent dispatch
    agents_dispatched: int                  # total agents called
    agents_with_live_data: int             # agents that returned score > 0

    # From Validation Gate
    anomaly_flag_count: int                # count of |Z| > 4.0 flags this cycle
    consistency_warning_count: int         # count of cross-source divergence warnings

    # From HMM Regime Detector (Session 09)
    regime_transition_probability: float | None  # None if HMM not yet trained

    # From Validation Gate cross-source check
    max_price_divergence_pct: float | None  # None if only one provider active


class PipelineConfidenceCalculator:
    """Computes deterministic pipeline_confidence from existing pipeline signals.

    This class has no I/O. It receives pre-computed values from the
    Orchestrator and returns a float in [0.0, 1.0] plus the breakdown.

    RULE: This class must never call any provider, agent, LLM, or
    database. All inputs arrive via PipelineConfidenceInputs. Pure function.
    """

    # Dimension weights — must sum to 1.0
    WEIGHTS = {
        "conviction_tightness": 0.30,
        "agent_coverage":       0.25,
        "gate_cleanliness":     0.20,
        "regime_stability":     0.15,
        "provider_agreement":   0.10,
    }

    # Gate thresholds — configurable via ModelStackConfig
    SKIP_THRESHOLD     = 0.40
    REDUCED_THRESHOLD  = 0.65
    HUMAN_REVIEW_CONVICTION_MIN = 140
    HUMAN_REVIEW_CONFIDENCE_MAX = 0.50

    def calculate(
        self,
        inputs: PipelineConfidenceInputs,
    ) -> tuple[float, ConfidenceDimensions]:
        """Compute pipeline_confidence and dimension breakdown.

        Args:
            inputs: Pre-computed pipeline signals from Orchestrator.

        Returns:
            Tuple of (pipeline_confidence: float, dimensions: ConfidenceDimensions).
            pipeline_confidence is in [0.0, 1.0].
        """
        dims = ConfidenceDimensions(
            conviction_tightness=self._conviction_tightness(
                inputs.conviction_point_estimate,
                inputs.conviction_lower,
            ),
            agent_coverage=self._agent_coverage(
                inputs.agents_with_live_data,
                inputs.agents_dispatched,
            ),
            gate_cleanliness=self._gate_cleanliness(
                inputs.anomaly_flag_count,
                inputs.consistency_warning_count,
            ),
            regime_stability=self._regime_stability(
                inputs.regime_transition_probability,
            ),
            provider_agreement=self._provider_agreement(
                inputs.max_price_divergence_pct,
            ),
        )

        confidence = (
            self.WEIGHTS["conviction_tightness"] * dims.conviction_tightness +
            self.WEIGHTS["agent_coverage"]       * dims.agent_coverage +
            self.WEIGHTS["gate_cleanliness"]     * dims.gate_cleanliness +
            self.WEIGHTS["regime_stability"]     * dims.regime_stability +
            self.WEIGHTS["provider_agreement"]   * dims.provider_agreement
        )

        return round(float(confidence), 4), dims

    def classify_tier(
        self,
        confidence: float,
        conviction: int,
    ) -> tuple[ConfidenceTier, float, bool]:
        """Determine confidence tier, position_size_modifier, and human_review_flag.

        Args:
            confidence: Output of calculate().
            conviction: Raw conviction point estimate (total_score).

        Returns:
            Tuple of (tier, position_size_modifier, human_review_flag).
        """
        if confidence < self.SKIP_THRESHOLD:
            return ConfidenceTier.SKIP, 0.0, False

        human_review = (
            conviction >= self.HUMAN_REVIEW_CONVICTION_MIN
            and confidence < self.HUMAN_REVIEW_CONFIDENCE_MAX
        )

        if confidence < self.REDUCED_THRESHOLD:
            return ConfidenceTier.REDUCED, 0.50, human_review

        return ConfidenceTier.STANDARD, 1.0, human_review

    # ── Private dimension calculators ──────────────────────────────────

    def _conviction_tightness(
        self, point_estimate: int, lower: int | None
    ) -> float:
        """Width of conviction interval relative to point estimate.

        If CQR is not yet trained (lower is None), return 0.5 (neutral).
        A tight interval (lower close to point estimate) scores near 1.0.
        A wide interval scores near 0.0.
        Maximum meaningful spread is capped at the full 220-point scale.
        """
        if lower is None or point_estimate == 0:
            return 0.5
        spread = point_estimate - lower
        return max(0.0, 1.0 - (spread / 220.0))

    def _agent_coverage(self, live: int, dispatched: int) -> float:
        """Fraction of agents that returned live data this cycle."""
        if dispatched == 0:
            return 0.0
        return min(1.0, live / dispatched)

    def _gate_cleanliness(self, anomaly_flags: int, consistency_warnings: int) -> float:
        """Absence of Validation Gate flags.

        Penalty per flag is linear. 5+ total flags → 0.0.
        0 flags → 1.0.
        """
        total_flags = anomaly_flags + consistency_warnings
        return max(0.0, 1.0 - (total_flags / 5.0))

    def _regime_stability(self, transition_prob: float | None) -> float:
        """Stability of current regime (inverse of transition probability).

        If HMM not yet trained, return 0.5 (neutral — no information).
        transition_probability near 0 → regime stable → score near 1.0.
        transition_probability near 1 → regime unstable → score near 0.0.
        """
        if transition_prob is None:
            return 0.5
        return max(0.0, 1.0 - float(transition_prob))

    def _provider_agreement(self, max_divergence_pct: float | None) -> float:
        """Cross-source provider agreement.

        If only one provider active (no cross-check possible), return 0.7
        (slight penalty for reduced redundancy, not a full miss).
        0% divergence → 1.0. >= 2% divergence → 0.0 (per Validation Gate threshold).
        """
        if max_divergence_pct is None:
            return 0.7
        return max(0.0, 1.0 - (max_divergence_pct / 2.0))
```

---

## Task 3 — Orchestrator integration

In `pipeline/orchestrator.py`, after `ConfluenceScorer.score()` returns:

```python
# Collect confidence inputs — all values are already available at this point
confidence_inputs = PipelineConfidenceInputs(
    conviction_point_estimate=result.total_score,
    conviction_lower=result.conviction_lower,
    agents_dispatched=len(agent_results),
    agents_with_live_data=sum(1 for r in agent_results if r.score > 0),
    anomaly_flag_count=validation_gate_context.anomaly_flag_count,
    consistency_warning_count=validation_gate_context.consistency_warning_count,
    regime_transition_probability=regime_result.transition_probability if regime_result else None,
    max_price_divergence_pct=validation_gate_context.max_price_divergence_pct,
)

calculator = PipelineConfidenceCalculator()
confidence, dimensions = calculator.calculate(confidence_inputs)
tier, size_modifier, human_review = calculator.classify_tier(
    confidence, result.total_score
)

# Attach to result (creates new frozen instance — ConfluenceResult is immutable)
result = result.model_copy(update=dict(
    pipeline_confidence=confidence,
    confidence_dimensions=dimensions,
    confidence_tier=tier,
    position_size_modifier=size_modifier,
    human_review_flag=human_review,
))

# Gate: skip signal entirely if confidence below floor
if tier == ConfidenceTier.SKIP:
    await self._log_confidence_skip(result, confidence_inputs)
    return None   # Orchestrator returns None → no signal emitted, no re-query

# Log for all signals (including REDUCED and STANDARD)
logger.info(
    "confidence_gate",
    tier=tier.value,
    pipeline_confidence=confidence,
    conviction=result.total_score,
    position_size_modifier=size_modifier,
    human_review=human_review,
    **dimensions.model_dump(),
)
```

---

## Task 4 — Config additions

Add to `atlas/shared/config.py` `ModelStackConfig`:

```python
# ─── CONFIDENCE GATE ──────────────────────────────────────────────────
confidence_gate_skip_threshold:    float = 0.40  # Below this → discard signal
confidence_gate_reduced_threshold: float = 0.65  # Below this → 0.5x position modifier
confidence_gate_human_conviction_min: int = 140  # High-stakes conviction floor
confidence_gate_human_confidence_max: float = 0.50  # Paradox threshold for human flag
```

`PipelineConfidenceCalculator` reads thresholds from config at instantiation.
No thresholds may be hard-coded in the calculator class.

---

## Task 5 — Tests

Create `atlas/ml/test_confidence_gate.py`:

```python
# Test 1: Perfect inputs → confidence near 1.0
# conviction=160, lower=155 (tight), all 8 agents live, 0 flags,
# transition_prob=0.05, 0% divergence
# → confidence >= 0.90

# Test 2: Degraded inputs → SKIP tier
# conviction=145, lower=90 (wide), 3/8 agents live, 4 anomaly flags,
# transition_prob=0.70, 1.8% divergence
# → tier == SKIP, position_size_modifier == 0.0

# Test 3: Moderate inputs → REDUCED tier
# conviction=130, lower=112, 6/8 agents live, 1 flag, transition_prob=0.35
# → tier == REDUCED, position_size_modifier == 0.50

# Test 4: Human review paradox
# conviction=155 (>= 140), pipeline_confidence=0.44 (< 0.50)
# → human_review_flag == True

# Test 5: No CQR trained (conviction_lower=None) → conviction_tightness == 0.5
# No HMM trained (transition_probability=None) → regime_stability == 0.5
# Single provider (max_price_divergence_pct=None) → provider_agreement == 0.7

# Test 6: Zero agents dispatched → agent_coverage == 0.0 (no ZeroDivisionError)

# Test 7: Weights sum to 1.0 (config integrity check)
# sum(PipelineConfidenceCalculator.WEIGHTS.values()) == 1.0

# Test 8: All dimension scores are in [0.0, 1.0] for any valid input
# Property-based: fuzz all numeric inputs across valid ranges
```

Run with: `pytest atlas/ml/test_confidence_gate.py -v --tb=short`

---

## Quality Gates

```bash
# All tests pass
pytest atlas/ml/test_confidence_gate.py -v

# ConfluenceResult backward compatibility — existing consumers must not break
# The new fields all have defaults. Verify:
python -c "
from atlas.pipeline.scorer import ConfluenceResult
# Instantiate with only pre-v2.3 fields — must not raise
r = ConfluenceResult(total_score=120, breakdown={}, regime_multiplier=1.0,
    sentiment_was_gated=False, signal_strength='MODERATE', position_size_pct=0.02,
    bias='LONG', reasoning='test', data_source_map={},
    shadow_metrics=None, version='2.3')
assert r.pipeline_confidence == 0.0
assert r.confidence_tier.value == 'STANDARD'
print('Backward compat: OK')
"

# No LLM calls inside confidence calculator
grep -rn "deepseek\|grok\|llm_client\|complete(" atlas/ml/confidence_gate.py
# Must return zero results

# No hard-coded thresholds
grep -rn "0\.40\|0\.65\|0\.50" atlas/ml/confidence_gate.py | grep -v "test_\|config"
# Must return zero results — thresholds read from config only

# Type safety
pyright atlas/ml/confidence_gate.py --pythonversion 3.12
```

---

## Files Created / Modified

| File | Action | Purpose |
|------|--------|---------|
| `atlas/pipeline/scorer.py` | MODIFY | Add `ConfidenceDimensions`, `ConfidenceTier` models; extend `ConfluenceResult` with v2.3 fields |
| `atlas/ml/confidence_gate.py` | CREATE | `PipelineConfidenceInputs`, `PipelineConfidenceCalculator` |
| `atlas/shared/config.py` | MODIFY | Add 4 confidence gate threshold settings |
| `atlas/pipeline/orchestrator.py` | MODIFY | Collect inputs, call calculator, apply gate, emit skip log |
| `atlas/ml/test_confidence_gate.py` | CREATE | 8 tests covering all tiers, edge cases, and backward compat |

---

## RULE
`PipelineConfidenceCalculator` must have zero I/O. It is a pure function.
If you find any provider call, Redis access, database query, or LLM call
inside `confidence_gate.py`, it is a bug. All inputs arrive pre-computed
via `PipelineConfidenceInputs`.
