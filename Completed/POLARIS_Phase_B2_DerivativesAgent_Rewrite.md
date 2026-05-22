# SESSION B2 — DerivativesAgent Rewrite (FINCON Compliant)

## Context Files
@agents/tier1/derivatives.py @atlas/shared/config.py @atlas/models/signal.py

## Goal
Rewrite the `DerivativesAgent` to score actual data values rather than column presence. Apply non-linear statistical gating to Funding Rates and Open Interest. 

**ARCHITECTURAL ALIGNMENT:** - MUST align with the Session 13 FINCON Hierarchy: `DerivativesAgent` max points is **50**.
- MUST NOT score liquidations (Liquidations belong to the `MarketMicrostructureAgent` via the HYDRA stream).
- Scores: Funding Rate Z-Score (25 pts), Open Interest Composite (15 pts), Futures Basis (10 pts) = 50 pts total.

---

## Task 1 — Update CategoryScores & Config
Ensure `atlas/shared/config.py` and `CategoryScores` in `signal.py` reflect the exact Tier-1 FINCON weights established in Session 13:
- Market Microstructure: 55
- Derivatives: 50
- On-Chain Intelligence: 35
- Sentiment & News: 40
- Macro & Cross-Market: 40
*(Total = 220)*

## Task 2 — Update DerivativesAgent Initialization
In `agents/tier1/derivatives.py`, ensure the agent initializes with `max_points=50`.

## Task 3 — Implement Extraction Helpers
Add `_extract_float()` and `_extract_str()` static methods to safely pull scalar values from the 1-row provider LazyFrame (e.g., CoinGlass derivatives data). If a column is missing, return `0.0` or `""` to prevent crashes.

## Task 4 — Implement Sub-Scoring Logic
Rewrite the internal scoring methods. They must take **scalar values**, not dataframes:

1. **`_score_funding(zscore: float) -> float` (Max 25 pts)**
   - *Non-Linear Gate:* Funding rates have no predictive power in the neutral band. 
   - If `|zscore| >= 2.5` -> 25.0 pts ("EXTREME")
   - If `|zscore| >= 1.5` -> 12.5 pts ("ELEVATED")
   - If `|zscore| < 1.5` -> 0.0 pts ("NEUTRAL - Noise Band")

2. **`_score_open_interest(oi_change_4h: float, oi_trend: str) -> float` (Max 15 pts)**
   - Expanding OI aligned with price = conviction. Expanding OI diverging from price = trap.
   - If `expanding` and `> 3.0%` -> 15.0 pts.
   - If `expanding` and `> 1.0%` -> 8.0 pts.
   - Otherwise -> 0.0 to 4.0 pts.

3. **`_score_basis(basis_annualised: float, basis_signal: str) -> float` (Max 10 pts)**
   - `backwardation` -> 10.0 pts (strong long signal).
   - `contango` > 30% -> 10.0 pts (extreme carry / crash risk).
   - `neutral` -> 4.0 pts.

## Task 5 — Update Main `score()` Method
Update the `score()` method to collect the dataframe, extract the scalars using the helpers, and pass them to the three new `_score_*` methods. Append human-readable explanations for the RAG RRF fusion downstream.

## Quality Gates
1. `pytest agents/tier1/test_derivatives.py -v --tb=short`
   - Test: `|zscore| = 0.8` returns exactly 0.0 points.
   - Test: `backwardation` returns max basis points.
   - Test: Maximum possible score does not exceed 50.0.
2. `pyright agents/tier1/derivatives.py --pythonversion 3.12` — zero errors.
3. `grep -i "liquidation" agents/tier1/derivatives.py` — MUST return zero results.