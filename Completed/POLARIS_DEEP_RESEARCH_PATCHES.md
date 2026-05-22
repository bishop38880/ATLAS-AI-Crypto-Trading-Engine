# POLARIS — Deep Research Patch Bundle (Corrected)
## Run AFTER the core 18-session roadmap is complete.

---

# PATCH A: Institutional Safety (MAD Z-Score & Adaptive Breakers)
**Targets:** `atlas/core/anomaly_detector.py`, `atlas/core/validation_gate.py`, `atlas/core/circuit_breaker.py`

## Task 1 — MAD Z-Score & Hard Blocks
- **anomaly_detector.py:** Replace the standard Z-score calculation with the **Median Absolute Deviation (MAD)**. Crypto distributions have fat tails; MAD prevents flash-crashes from skewing the rolling standard deviation.
  - Formula: `mad_z = 0.6745 * (value - median) / mad`
- **Dual Thresholds:** Add regime-conditional bands. Normal: Flag at 3.5, Hard Block at 6.0. Volatile: Flag at 5.0, Hard Block at 8.0.
- **validation_gate.py:** If data exceeds the Hard Block threshold, reject it completely (treat it identical to STALE data) to protect the LLM. Add a strict `source_timestamp` requirement to all payloads.

## Task 2 — Adaptive Circuit Breakers
- **circuit_breaker.py:** Replace `pybreaker` with a custom sliding window breaker. 
- Introduce a **DEGRADED** state. If a provider starts returning slow calls (>3s latency) but isn't completely dead, flip to `DEGRADED`.
- In `DEGRADED` state, only allow critical-path endpoint calls (e.g., price, funding rates) and block non-essential calls (e.g., social graphs) to save bandwidth and prevent cascading timeouts.

---

# PATCH B: 3-Tier LLM Gating (Cost Optimization)
**Targets:** `atlas/core/complexity_router.py`, `atlas/shared/config.py`

## Task 1 — Smart Escalation
- **config.py:** Add `router_gated_threshold: int = 140`, `router_mandatory_threshold: int = 170`, and `router_gated_confidence_threshold: float = 0.75`.
- **complexity_router.py:** Replace the binary router with three tiers:
  - **FAST (< 140):** Route to DeepSeek V3 (`deepseek-chat`).
  - **MANDATORY (>= 170 or anomaly flagged):** Route directly to DeepSeek R1 (`deepseek-reasoner`).
  - **GATED (140 - 169):** Route to DeepSeek V3 first. Parse the response text for confidence (e.g., "I am 85% confident"). If confidence is >= 0.75, keep the V3 response. If it is < 0.75, escalate the prompt to DeepSeek R1.

---

# PATCH C: FINCON Orchestrator Concurrency
**Targets:** `pipeline/hierarchical_orchestrator.py`

## Task 1 — FIRST_COMPLETED Staggered Collection
- Update Phase 1 of the Orchestrator (the 5 Tier-1 Analysts).
- Replace `asyncio.gather()` with `asyncio.wait(return_when=asyncio.FIRST_COMPLETED)`.
- **Per-Agent Timeouts:**
  - `MarketMicrostructureAgent`: 4.0s
  - `SentimentNewsAgent`: 4.0s
  - `DerivativesAgent`: 4.0s
  - `MacroCrossMarketAgent`: 5.0s
  - `OnChainIntelligenceAgent`: 8.0s (Nansen takes longer)
- If the Fast-Path `RiskAssessmentAgent` issues a veto while these Tier-1 agents are processing, instantly call `.cancel()` on all pending tasks to free up CPU/Network resources.

---

# PATCH D: Portfolio-Aware Risk & Sizing
**Targets:** `agents/risk/risk_agent.py`, `agents/risk/portfolio_optimisation_agent.py`

## Task 1 — Drawdown Shutdowns (Tier 2 Risk)
- **risk_agent.py:** Add `_check_portfolio_drawdown()`. Read trailing PnL from Redis. 
  - 1h PnL < -3% -> Veto new INCREASE positions.
  - 4h PnL < -5% -> Veto ALL new positions.
  - 24h PnL < -10% -> `DRAWDOWN_SHUTDOWN`. Trigger the standalone Kill Switch (Session 22) and halt the system.

## Task 2 — Correlation & Sector Sizing (Tier 2 Portfolio)
- **portfolio_optimisation_agent.py:** The Risk Agent vetos; the Portfolio Agent sizes. 
- Read `portfolio:avg_pairwise_correlation` from Redis (populated by the MacroCrossMarketAgent). 
  - If average correlation > 0.70 (market stress), slice all proposed position sizes by 50%.
- Enforce Sector Caps: Map assets to sectors (e.g., SOL/AVAX -> `L1_ALTS`). Cap exposure to 25% per sector in normal regimes, and 15% in high-correlation regimes.

## Quality Gates
1. `pytest atlas/core/test_anomaly_detector.py -v` — verify MAD math.
2. `pytest pipeline/test_hierarchical_orchestrator.py -v` — verify `FIRST_COMPLETED` cancellation logic on risk veto.
3. `pyright atlas/ --pythonversion 3.12` — zero errors.
