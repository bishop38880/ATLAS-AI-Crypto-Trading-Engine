# SESSION 19 — RLVR: Reinforcement Learning with Verifiable Rewards

## Context Files
@pipeline/hierarchical_orchestrator.py @agents/synthesiser/signal_synthesiser.py @atlas/shared/config.py @atlas/signals/outcome.py @atlas/ml/meta_learner.py @atlas/ml/thompson_sampling.py

## Prerequisites
ALL prior sessions must be complete. This is the most complex session in the
roadmap. Session 19 is split into **19A (Foundation)** and **19B (Training Layers)**.

## Architectural Context

Trade outcomes are objectively verifiable — P&L is ground truth. RLVR introduces
temporal awareness: the policy learns that taking a BTC long NOW affects the
optimal action on the NEXT ETH signal (via portfolio exposure).

RLVR does NOT delete Sessions 08, 10, or 12 — it consumes their outputs as state
features.

### Hard Safety Constraints

**RULE 1:** Risk Agent veto is ABSOLUTE. RLVR policy output is discarded on veto.
**RULE 2:** RLVR runs in SHADOW MODE by default (`RLVR_LIVE: bool = False`). Only
the base scorer's signal is emitted to PROMETHEUS.
**RULE 3:** RLVR must degrade gracefully. If policy fails, base scorer continues
unaffected.

---

## NON-NEGOTIABLE INVARIANTS

1. **Pyright only.** Run `pyright --pythonversion 3.12`.
2. **No banned libraries.** `aioredis`, `pandas`, `orjson`, stdlib `json`,
   `pickle`, `joblib`, `sentence-transformers`, `FAISS`, `BM25`, `pgvector`,
   `SQLAlchemy`, `psycopg2`, `requests`, `LlamaIndex` → all banned.
   **Additionally:** `gymnasium`, `stable-baselines3`, and any RL framework are
   **BANNED**. PPO implementation is custom (~200 lines).
3. **`PolarisSettings` only.**
4. **40-line function limit.**
5. **Loguru only.** No f-strings in loggers.
6. **ATLAS has ZERO exchange awareness.**
7. **Test floor is sacred.**
8. **THE ASYNC ML BOUNDARY.** `torch` forward passes and all numpy/scipy
   operations MUST be wrapped in `asyncio.to_thread(...)` when called from
   async context. torch is CPU-only — no CUDA. The RTX 5060 Ti 16GB is reserved
   for the local LLM.
9. **FINCON mapping.** State encoder maps to the **5 Tier-1 Analyst agents**
   (FINCON Session 13), not the legacy 10-agent flat architecture.
10. **Model serialization.** Use `torch.save()` / `torch.load()` for policy
    checkpoints. `pickle` and `joblib` banned for all other serialization.
11. **Weight adjustment discipline.** `weight_adjustments` are clamped to
    `[-0.1, +0.1]` per agent with a **net-zero sum constraint enforced via
    projection**, not rejection sampling. See Task 2 for exact algorithm.
12. **Warm-start mapping is explicit.** See Task 5 for the precise initialization
    protocol from Session 08 meta-learner + Session 12 Thompson priors.

---

## SESSION 19A — Foundation (State, Action, Reward)

### Task 1 — Observation Space: State Encoder

Create `atlas/ml/rlvr/state_encoder.py`:

`RLVRState` Pydantic model (`frozen=True`) — **42 dimensions** mapping to 5
Tier-1 Analysts (Technical, Derivatives, OnChain, Sentiment, MarketRegime):

```python
class RLVRState(BaseModel, frozen=True):
    # === AGENT VERDICTS (15 dims) ===
    agent_scores: list[float] = Field(min_length=5, max_length=5)       # 5 analyst scores
    agent_confidences: list[float] = Field(min_length=5, max_length=5)  # 5 confidences
    agent_directions: list[float] = Field(min_length=5, max_length=5)   # 5 directions in [-1, +1]

    # === MARKET REGIME (6 dims) ===
    regime_bull_prob: float
    regime_bear_prob: float
    regime_volatile_prob: float
    regime_duration_normalized: float
    regime_transition_prob: float
    volatility_zscore: float

    # === PORTFOLIO CONTEXT (6 dims) ===
    portfolio_exposure: float
    position_count_normalized: float
    portfolio_direction: float
    portfolio_correlation: float
    consecutive_losses_normalized: float
    drawdown_fraction: float

    # === SCORING CONTEXT (5 dims) ===
    base_conviction: float
    meta_learner_score: float
    thompson_entropy: float
    hard_point_fraction: float
    macro_suppression: float

    # === TEMPORAL (4 dims) ===
    hour_sin: float
    hour_cos: float
    dow_sin: float
    dow_cos: float

    # === RECENT PERFORMANCE (6 dims) ===
    recent_win_rate: float
    recent_avg_pnl: float
    recent_sharpe: float
    recent_good_win_rate: float
    recent_bad_loss_rate: float
    signal_frequency: float
```

Total: 15 + 6 + 6 + 5 + 4 + 6 = **42 dimensions**. `STATE_DIM = 42` constant in
`state_encoder.py`. Normalization is deterministic and stateless.

### Task 2 — Action Space: Action Decoder

Create `atlas/ml/rlvr/action_decoder.py`:

```python
class RLVRAction(BaseModel, frozen=True):
    trade_decision: Literal["LONG", "SHORT", "ABSTAIN"]
    trade_confidence: float = Field(ge=0.0, le=1.0)
    weight_adjustments: list[float] = Field(min_length=5, max_length=5)
```

### Net-Zero Projection (authoritative algorithm)

```python
import numpy as np

def project_weight_adjustments(raw: np.ndarray, clip: float = 0.1) -> np.ndarray:
    """Project raw policy output to satisfy (a) |a_i| <= clip, (b) sum(a) == 0.

    Algorithm:
    1. Clip to [-clip, +clip].
    2. Subtract mean to enforce zero-sum.
    3. Re-clip (mean subtraction may push values out of range).
    4. Final zero-sum enforcement via single scalar adjustment on the agent
       with the largest absolute value (ensures feasibility).
    """
    a = np.clip(raw, -clip, clip)
    a = a - a.mean()
    a = np.clip(a, -clip, clip)
    residual = a.sum()
    if abs(residual) > 1e-9:
        idx = int(np.argmax(np.abs(a)))
        a[idx] -= residual
        a = np.clip(a, -clip, clip)
    return a
```

**NOT rejection sampling.** Projection is deterministic and O(1).

### Task 3 — Reward Shaper

Create `atlas/ml/rlvr/reward_shaper.py`:

`RewardShaper` class using verified P&L from Post-Trade Learning (Session 11A):

```
reward = shaped_pnl + risk_penalty + calibration_bonus + abstention_reward
```

- Asymmetric: losses penalized more heavily than gains rewarded (loss factor 1.5×).
- `abstention_reward = +0.01` when the base scorer would have entered a losing
  trade but the policy chose ABSTAIN.

---

## SESSION 19B — Training Layers (Policy, Shadow, Evaluation)

### Task 4 — Policy Network

Create `atlas/ml/rlvr/policy_network.py`:

- `SignalPolicyNetwork`: 42 → 64 → 32 → action_dim MLP (~19K params).
- `torch` CPU-only, `float32`.
- Forward pass must complete in < 5ms on CPU.
- Wrapped in `asyncio.to_thread()` when called from async context.
- Separate heads for decision logits, confidence, and weight adjustments.

### Task 5 — PPO Trainer (with explicit warm-start)

Create `atlas/ml/rlvr/trainer.py`:

Custom PPO implementation (~200 lines) — NO external RL frameworks.

### Warm-Start Protocol

Policy initialization uses Sessions 08 + 12 outputs:

1. **Decision head bias** ← initialized from the sign of the meta-learner's
   current confidence-weighted aggregate:
   - If meta-learner tends to be bullish on recent history: bias LONG logit +0.5,
     SHORT logit −0.5, ABSTAIN logit 0.
   - If bearish: reverse.
   - If neutral: all zeros.

2. **Weight-adjustment head** ← initialized to **zero bias, small Gaussian
   weights** (`std=0.01`). This is critical — the policy must start "close
   to identity" (minimal adjustment from current meta-learner weights). It
   learns adjustments from this starting point, not from scratch.

3. **Confidence head** ← initialized to match the current Thompson Sampling
   posterior entropy: higher entropy → lower initial confidence bias.
   `confidence_bias = 1.0 - normalized_thompson_entropy`.

4. **Backbone MLP** ← standard Kaiming initialization.

All four warm-start steps happen inside `trainer.__init__`. Document the
initialization in code with explicit comments referencing this section.

### Task 6 — Shadow Runner

Create `atlas/ml/rlvr/shadow_runner.py`:

- Runs alongside base scorer, produces parallel recommendations.
- Logs both base and RLVR decisions to PostgreSQL via `asyncpg`.
- NEVER applies RLVR weights to live scorer unless `RLVR_LIVE=True`.
- Gracefully skips on policy inference failure (logs warning, base scorer continues).

### Task 7 — A/B Evaluator

Create `atlas/ml/rlvr/evaluator.py`:

- `EvaluationReport` Pydantic model comparing base vs. RLVR on: win rate, Sharpe,
  avg PnL, regime breakdown.
- Permutation test for statistical significance (p < 0.05).
- The evaluator NEVER recommends switching to live mode automatically —
  promotion is human-in-the-loop only.

---

## Quality Gates

### Unit Tests
```bash
pytest atlas/ml/rlvr/test_state_encoder.py -v
pytest atlas/ml/rlvr/test_action_decoder.py -v
pytest atlas/ml/rlvr/test_reward_shaper.py -v
pytest atlas/ml/rlvr/test_policy_network.py -v
pytest atlas/ml/rlvr/test_trainer.py -v
pytest atlas/ml/rlvr/test_shadow_runner.py -v
pytest atlas/ml/rlvr/test_evaluator.py -v
```

### Type Safety
```bash
pyright atlas/ml/rlvr/ --pythonversion 3.12
```

### Safety Verification
```bash
grep -rn "RLVR_LIVE\|rlvr_live" atlas/shared/config.py   # must show default=False
grep -rn "risk_veto" atlas/ml/rlvr/ | grep -v test_       # every occurrence must check veto
```

### Architecture Compliance
```bash
grep -rn "gymnasium\|stable.baselines\|stable_baselines" atlas/ --include="*.py"  # zero
grep -rn "bitget\|ccxt\|order\|execute" atlas/ml/rlvr/ --include="*.py"           # zero
grep -rn "\.cuda()\|device=.cuda" atlas/ml/rlvr/ --include="*.py"                  # zero
```

### Projection Correctness Test
```python
def test_projection_is_deterministic_and_feasible():
    rng = np.random.default_rng(42)
    for _ in range(1000):
        raw = rng.uniform(-0.5, 0.5, size=5)
        adjusted = project_weight_adjustments(raw, clip=0.1)
        assert np.all(np.abs(adjusted) <= 0.1 + 1e-9)
        assert abs(adjusted.sum()) < 1e-6
```

## Dependencies Added to pyproject.toml
```toml
"torch>=2.2",   # CPU-only: pip install torch --index-url https://download.pytorch.org/whl/cpu
"scipy>=1.12",  # Permutation tests in evaluator
```

Do NOT add `gymnasium`, `stable-baselines3`, or any RL framework.

## Anti-Pattern Checklist
- [ ] State encoder maps to **5 FINCON Tier-1 analysts**, NOT 10 legacy agents
- [ ] No `gymnasium` or `stable-baselines3`
- [ ] `torch` CPU-only — no CUDA imports, no VRAM references
- - [ ] No pickle or joblib (except torch.save / torch.load with weights_only=True for policy).
- [ ] Forward pass wrapped in `asyncio.to_thread()`
- [ ] `RLVR_LIVE` defaults to `False`
- [ ] Risk veto is absolute — RLVR never overrides
- [ ] Shadow runner logs only, never modifies live scorer
- [ ] Weight adjustments use **projection** (deterministic), not rejection sampling
- [ ] Warm-start: decision head from meta-learner sign, weight-head from zero + small
      Gaussian, confidence head from Thompson entropy
- [ ] No `import aioredis` — `redis.asyncio`
- [ ] All functions ≤ 40 lines
