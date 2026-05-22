# SESSION 09B — Supporting Intelligence: Funding Rate Monitor + News & Macro Agent

## Context Files
@agents/base.py @pipeline/scorer.py @atlas/config.py @agents/registry.json

## Prerequisites
Sessions 01–09A complete. All 8 scoring agents (TECHNICAL, DERIVATIVES, ONCHAIN, SENTIMENT, WHALE, LIQUIDATION, REGIME, CORRELATION) plus the veto-only Risk agent must be registered and passing tests.

**NOTE:** Session 09A (defining the core analyst agents that produce TECHNICAL/DERIVATIVES/ONCHAIN/SENTIMENT/WHALE/LIQUIDATION/REGIME/CORRELATION results) is a prerequisite but is not in the immediate upload set — ensure it has been produced and run before this session.

## Goal
Build the final 2 of 10 scoring agents. The News & Macro agent acts as a system-wide
conviction SUPPRESSOR, dampening conviction during high-impact macro events.

---

## CRITICAL ARCHITECTURAL REMINDER — Risk is VETO-ONLY

Earlier versions of this session had a `CATEGORY_WEIGHTS` table that claimed
10 categories summed to 1.00, while Session 04A claimed the Risk agent had
35% weight. Total implied weight was 1.35 (135%). This was a bug.

The correct architecture (as fixed in Session 04A) is:
- **10 scoring agents** have `CATEGORY_WEIGHTS` summing to 1.00 = 220 points
- **Risk agent** is veto-only with `max_points=0` — it does NOT appear in
  `CATEGORY_WEIGHTS` and does NOT contribute points to the confluence score
- Risk's sole output is a binary `veto: bool` flag evaluated BEFORE the
  weighted sum in the scorer

The `CATEGORY_WEIGHTS` table in this session reflects that design: 10
scoring agents only, summing to 1.00, Risk excluded by design.

---

## NON-NEGOTIABLE INVARIANTS (read before writing any code)
1. **Pyright only.** Ignore any legacy references to `mypy`. Run `pyright --pythonversion 3.12`.
2. **No banned libraries.** `aioredis`, `pandas`, `requests`, `orjson`, stdlib `json`, `FAISS`, `BM25`, `SQLAlchemy`, `psycopg2`, `pickle`, `joblib`, `sentence-transformers` are all **BANNED**. Use `redis.asyncio` for Redis, `msgspec` for JSON, `asyncpg` for PostgreSQL.
3. **`PolarisSettings` only.** Never use `os.getenv()`.
4. **40-line function limit.** Extract helpers.
5. **Loguru only.** No `print()`, no stdlib `logging`.
6. **ATLAS has ZERO exchange awareness.** No orders, positions, credentials, or execution logic.
7. **Test floor is sacred.** `pytest` count must not decrease.
8. **`Decimal` for all financial fields.** Funding rates, basis values, and any price-derived numerics must use `Decimal`. Cast to `float` only at the agent score output boundary.
9. **Data provider rules:**
   - **Funding rates & OI:** Read from **Coinalyze** provider (multi-key round-robin rate limiter, Redis sliding window). CoinGlass has been **removed entirely** from the stack — no V3 or V4 references.
   - **Liquidation data:** Read from **HYDRA** local buffer only. No external HTTP calls for liquidation data.
10. **Risk is veto-only (not in CATEGORY_WEIGHTS).** The `CATEGORY_WEIGHTS` table lists only the 10 scoring agents. Risk is NOT listed because it contributes 0 points to the score.

---

## Task 1 — Expand AgentCategory

Add to `agents/base.py`:
- `FUNDING = "funding"`
- `NEWS_MACRO = "news_macro"`

Ensure the `AgentCategory` enum now has all 11 categories: 10 scoring agents plus the veto-only RISK.

## Task 2 — Funding Rate & Basis Monitor

Create `agents/funding/funding_agent.py`:

- Inherits from `BaseAgent`, weight: 6% (13.2 of 220 points)
- **Feature 1: Funding Rate Extremes** — Mean-reversion signaling on overleveraged sides.
  - Read funding rates from **Coinalyze provider** (via Redis cache key pattern `coinalyze:funding:{asset}`)
  - Compute Z-score against 30-day rolling mean (stored in PostgreSQL via `asyncpg`)
  - |Z| > 2.0 → strong mean-reversion signal
  - |Z| > 3.0 → extreme, add urgency flag
  - All rate arithmetic in `Decimal`
- **Feature 2: Futures Basis** — Contango vs Backwardation detection
  - Read from Coinalyze basis data
  - Annualized basis > 20% → overheated longs
  - Annualized basis < -5% → capitulation signal
- **Feature 3: OI Divergence** — Open Interest vs Price matrix
  - Price ↑ + OI ↑ = trend confirmation (bullish)
  - Price ↑ + OI ↓ = short squeeze / weak rally
  - Price ↓ + OI ↑ = new shorts entering (bearish)
  - Price ↓ + OI ↓ = long liquidation cascade
  - Read OI data from **Coinalyze provider**

**CRITICAL NEGATIVE CONSTRAINT:** Do NOT import `coinglass`, do NOT reference CoinGlass V3/V4 endpoints, do NOT call any CoinGlass API. Coinalyze is the sole derivatives data provider.

## Task 3 — News & Macro Agent (Conviction Suppressor)

Create `agents/news_macro/news_macro_agent.py`:

- Inherits from `BaseAgent`, weight: 3% (6.6 of 220 points)
- **This agent's primary function is SUPPRESSION, not signal generation.**
- **Feature 1: Scheduled Event Window**
  - High-impact event (FOMC, CPI, NFP, PPI) within 60 mins sets `conviction_suppression = -20`
  - Medium-impact event within 60 mins sets `conviction_suppression = -10`
  - Event schedule sourced from FRED calendar data (already integrated in Session 14)
  - Store upcoming events in Redis: `macro:events:upcoming` with TTL matching next event time
- **Feature 2: Breaking News Impact**
  - High-impact negative news: `conviction_suppression = -15`, direction bias toward risk-off
  - High-impact positive news: `conviction_suppression = -5` (still suppress — news creates volatility)
  - News sentiment read from sentiment provider pipeline
- **Output contract:** The `AgentResult` must include `conviction_suppression: int` in its metadata dict. This value is read by the ConfluenceScorer in Task 4.

## Task 4 — Wire Conviction Suppression Into Scorer

Update `pipeline/scorer.py`:

- Read `conviction_suppression` from the `NewsMacroAgent` result metadata
- **Apply suppression AFTER the weighted sum / meta-learner output but BEFORE final `SignalOutput` construction**
- `final_score = max(0, base_score + conviction_suppression)` (suppression is negative, floor at 0)
- **Scorer DAG position:** This is Step 3 — applied after both the meta-learner path (Step 1) and the regime/decorrelation fallback path (Step 2). It is the last mathematical operation before the score becomes final.
- **The 220-point scoring system is NOT modified.** Suppression adjusts the conviction percentage, not the point allocation.
- Log suppression events: `logger.info("conviction_suppressed", amount=suppression, reason=reason, base=base, final=final)`

## Task 5 — Finalize Weight Map

Finalize `CATEGORY_WEIGHTS` in the scorer. This table contains ONLY the 10 scoring agents — Risk is NOT here because Risk is veto-only (`max_points=0`, see Session 04A).

| Agent Category | Weight | Points (of 220) |
|---------------|--------|-----------------|
| TECHNICAL     | 0.25   | 55.0            |
| DERIVATIVES   | 0.20   | 44.0            |
| ONCHAIN       | 0.12   | 26.4            |
| SENTIMENT     | 0.10   | 22.0            |
| WHALE         | 0.08   | 17.6            |
| LIQUIDATION   | 0.07   | 15.4            |
| REGIME        | 0.05   | 11.0            |
| FUNDING       | 0.06   | 13.2            |
| NEWS_MACRO    | 0.03   | 6.6             |
| CORRELATION   | 0.04   | 8.8             |
| **TOTAL (scoring agents)** | **1.00** | **220.0** |
| RISK (veto-only, not scored) | — | 0 |
| **GRAND TOTAL WEIGHT** | **1.00** | **220.0** |

In the scorer code, explicitly filter out the RISK category before computing the weighted sum:

```python
def _compute_weighted_sum(self, results: list[AgentResult]) -> float:
    scoring_results = [r for r in results if r.category != AgentCategory.RISK]
    return sum(
        r.score * CATEGORY_WEIGHTS[r.category.value]
        for r in scoring_results
    )
```

## Task 6 — Register Agents

Add both agents to `agents/registry.json`:
```json
{
  "name": "funding_rate_monitor",
  "category": "funding",
  "module": "agents.funding.funding_agent",
  "class": "FundingAgent",
  "weight": 0.06,
  "enabled": true
},
{
  "name": "news_macro_agent",
  "category": "news_macro",
  "module": "agents.news_macro.news_macro_agent",
  "class": "NewsMacroAgent",
  "weight": 0.03,
  "enabled": true
}
```

## Quality Gates
1. `pytest agents/funding/test_funding_agent.py -v` — all pass
   - Test: extreme funding rate (Z > 2.0) produces mean-reversion signal
   - Test: OI divergence matrix returns correct classification
   - Test: all rate values use `Decimal` internally
2. `pytest agents/news_macro/test_news_macro_agent.py -v` — all pass
   - Test: FOMC within 60 mins → `conviction_suppression = -20`
   - Test: no event → `conviction_suppression = 0`
   - Test: breaking negative news → suppression applied
3. `pytest pipeline/test_scorer.py -v` — all pass
   - Test: suppression reduces score but floors at 0
   - Test: no suppression → score unchanged
   - Test: `CATEGORY_WEIGHTS` (10 scoring entries) sums to 1.00 ±1e-9
   - Test: Risk agent result is filtered out of the weighted sum (scorer uses 10 categories, not 11)
   - Test: Risk agent veto=True STILL forces No Position (veto logic independent of weight filtering)
4. `pyright --pythonversion 3.12 agents/funding/ agents/news_macro/` — zero errors

## Anti-Pattern Checklist (verify before committing)
- [ ] No `import aioredis` — must be `import redis.asyncio`
- [ ] No `import json` — must be `import msgspec`
- [ ] No `os.getenv()` — must use `PolarisSettings`
- [ ] No `print()` — must use `logger` from Loguru
- [ ] No CoinGlass references anywhere — Coinalyze is the derivatives provider
- [ ] No external HTTP calls for liquidation data — HYDRA buffer only
- [ ] No `float` for funding rates internally — use `Decimal`, cast at boundary
- [ ] No `mypy` references — use `pyright`
- [ ] `CATEGORY_WEIGHTS` does NOT include RISK (RISK is veto-only)
- [ ] Scorer weighted-sum filters out `AgentCategory.RISK` explicitly
- [ ] All functions ≤ 40 lines
