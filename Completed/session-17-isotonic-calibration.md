# SESSION 17 — Isotonic Regression: Conviction Score Calibration

## Context Files
@pipeline/hierarchical_orchestrator.py @agents/synthesiser/signal_synthesiser.py @atlas/ml/meta_learner.py @atlas/shared/config.py

## Prerequisites
Sessions 08 (Meta-learner), 11A (Post-Trade Learning), and 13 (FINCON hierarchy)
must be complete.

## Goal
Raw conviction scores (0–100) are not calibrated probabilities. A score of 80
doesn't mean "80% chance of profit." Build an isotonic regression calibrator that
maps raw conviction to calibrated win probabilities. Target: ECE < 0.05.

**Architectural alignment:** Integration happens inside the `SignalSynthesiserAgent`
(Tier 3), which replaced the legacy `ConfluenceScorer`.

---

## NON-NEGOTIABLE INVARIANTS

1. **Pyright only.** Run `pyright --pythonversion 3.12`.
2. **No banned libraries.** `aioredis`, `pandas`, `orjson`, stdlib `json`, `pickle`,
   `joblib`, `sentence-transformers`, `FAISS`, `BM25`, `pgvector`, `SQLAlchemy`,
   `psycopg2`, `requests` → all banned.
   **Additionally for this session:** `pickle` and `joblib` are BANNED for model
   serialization — coefficients are stored via `msgspec` (see Task 2 for the
   exact extraction protocol). `matplotlib` is NOT approved — the reliability
   diagram uses pure-SVG generation (see Task 4).
3. **`PolarisSettings` only.**
4. **40-line function limit.**
5. **Loguru only.** No f-strings in loggers.
6. **ATLAS has ZERO exchange awareness.**
7. **Test floor is sacred.**
8. **THE ASYNC ML BOUNDARY.** `sklearn.isotonic.IsotonicRegression` is synchronous
   and CPU-bound. `.fit()`, `.predict()`, and `.transform()` MUST be wrapped in
   `await asyncio.to_thread(...)`. Calling them directly on the async event loop
   will block the multi-asset runner.

```python
# CORRECT
calibrated = await asyncio.to_thread(calibrator.calibrate, raw_score)
result = await asyncio.to_thread(calibrator.train, scores, outcomes)

# FATAL ERROR
calibrated = calibrator.calibrate(raw_score)  # BLOCKS EVENT LOOP
```

9. **Numpy output casting.** All `np.float64` values MUST be cast to native `float`
   before entering Pydantic models.
10. **Session 08 continuity.** The meta-learner from Session 08 still exists and
    produces the raw conviction score that feeds this calibrator. Session 13's
    `SignalSynthesiserAgent` wraps Session 08's scorer; it does not replace it.

---

## Task 1 — Calibration Dataset Builder

Create `atlas/ml/calibration_data.py`:

```python
import numpy as np
from datetime import datetime
from pydantic import BaseModel, Field


class CalibrationDataset(BaseModel, frozen=True):
    raw_scores: list[float]              # Native Python float, not np.float64
    outcomes: list[int]                  # 0 or 1
    n_samples: int = Field(ge=0)
    date_range: tuple[datetime, datetime]


class InsufficientDataError(Exception):
    pass


class CalibrationDataBuilder:
    async def build(self, min_samples: int = 500) -> CalibrationDataset:
        """Query PostgreSQL via asyncpg for historical signals with outcomes."""
        ...
```

- Queries PostgreSQL via `asyncpg` for all signals with recorded outcomes.
- `outcomes` derives from `GOOD_WIN=1`, `LUCKY_WIN=1`, `GOOD_LOSS=0`, `BAD_LOSS=0`
  (profitable vs. not).
- If fewer than `min_samples` rows: raise `InsufficientDataError`.
- All `np.float64` values cast to `float()` before constructing `CalibrationDataset`.

## Task 2 — Isotonic Calibrator

Create `atlas/ml/calibrator.py`:

### Coefficient Extraction Protocol

The canonical way to serialize an `sklearn.isotonic.IsotonicRegression` fit is
to extract the two threshold arrays — `X_thresholds_` and `y_thresholds_` — plus
the out-of-bounds policy. These are sufficient to reconstruct prediction via
linear interpolation without needing the original scikit-learn object.

```python
import msgspec

class CalibratorCoefficients(msgspec.Struct, frozen=True):
    """Serialisable coefficient bundle — fully round-trippable."""
    x_thresholds: list[float]      # Monotone increasing breakpoints
    y_thresholds: list[float]      # Corresponding predictions
    out_of_bounds: str             # "clip"
    trained_at_iso=datetime.now(datetime.UTC).isoformat(),
    training_samples: int
```

### Calibrator

```python
from decimal import Decimal
import numpy as np
from sklearn.isotonic import IsotonicRegression
from pydantic import BaseModel


class CalibrationBin(BaseModel, frozen=True):
    bin_lower: float
    bin_upper: float
    predicted_mean: float
    observed_rate: float
    n_samples: int


class CalibrationResult(BaseModel, frozen=True):
    ece: float
    bins: list[CalibrationBin]
    n_samples: int


class ConvictionCalibrator:
    def __init__(self) -> None:
        self._model = IsotonicRegression(out_of_bounds="clip")
        self._fitted = False

    def train(self, raw_scores: np.ndarray, outcomes: np.ndarray) -> CalibrationResult:
        """Synchronous — caller MUST wrap in asyncio.to_thread."""
        self._model.fit(raw_scores, outcomes)
        self._fitted = True
        ece, bins = self._compute_ece(raw_scores, outcomes, n_bins=10)
        return CalibrationResult(ece=float(ece), bins=bins, n_samples=len(raw_scores))

    def calibrate(self, raw_score: float) -> float:
        """Synchronous — caller MUST wrap in asyncio.to_thread."""
        if not self._fitted:
            return raw_score / 100.0  # Fallback: linear scaling
        return float(self._model.predict([raw_score])[0])

    def extract_coefficients(self) -> CalibratorCoefficients:
        """Returns msgspec-serialisable bundle for persistence."""
        return CalibratorCoefficients(
            x_thresholds=[float(x) for x in self._model.X_thresholds_],
            y_thresholds=[float(y) for y in self._model.y_thresholds_],
            out_of_bounds="clip",
            trained_at_iso=datetime.utcnow().isoformat(),
            training_samples=int(self._model.X_thresholds_.shape[0]),
        )

    def load_coefficients(self, coeffs: CalibratorCoefficients) -> None:
        """Reconstruct from serialised coefficients."""
        self._model = IsotonicRegression(out_of_bounds=coeffs.out_of_bounds)
        self._model.X_thresholds_ = np.asarray(coeffs.x_thresholds, dtype=np.float64)
        self._model.y_thresholds_ = np.asarray(coeffs.y_thresholds, dtype=np.float64)
        self._model.f_ = None  # sklearn rebuilds interpolator lazily on first predict
        self._fitted = True
```

**Persistence:**
- Save: `msgspec.json.encode(calibrator.extract_coefficients())` → PostgreSQL
  `calibrator_snapshots` table (JSONB column).
- Load: `msgspec.json.decode(row, type=CalibratorCoefficients)` →
  `calibrator.load_coefficients(...)`.

## Task 3 — Integration with Tier 3 Synthesiser

Update `SignalOutput` schema (already specified in Session IM-1):
- `calibrated_probability: float | None = None`
- `calibration_ece: float | None = None`

Update `SignalSynthesiserAgent`:
- After fusing final raw conviction, apply calibrator if model available:
  `calibrated = await asyncio.to_thread(calibrator.calibrate, raw_conviction)`
- Both raw and calibrated values populated in final `SignalOutput`.
- Log with kwargs: `logger.info("signal_calibrated", conviction=87, calibrated=0.73, ece=0.038)`.

## Task 4 — Pure-SVG Reliability Diagram

Create `atlas/ml/reliability_diagram.py`:

**No `matplotlib`.** Generate reliability diagrams as hand-written SVG strings.
SVG is a text format, renders in any browser, and has zero Python dependencies.

```python
def generate_reliability_diagram_svg(
    calibration_result: CalibrationResult,
    width: int = 400,
    height: int = 400,
) -> str:
    """Returns an SVG string.

    - X-axis: predicted probability (0.0 to 1.0)
    - Y-axis: observed frequency (0.0 to 1.0)
    - Diagonal line = perfect calibration
    - Plotted points = actual calibration bins from CalibrationResult
    """
    ...
```

- Axes: simple `<line>` elements
- Diagonal reference: `<line>` from `(padding, height-padding)` to
  `(width-padding, padding)`
- Data points: `<circle>` per bin, radius proportional to `n_samples`
- ECE value: `<text>` element in top-right

Persist in PostgreSQL as TEXT (JSONB column with `"format": "svg"` metadata) —
NOT as base64 PNG.

## Quality Gates
1. `pytest atlas/ml/test_calibrator.py -v` — all pass.
   - Synthetic well-calibrated data → ECE < 0.03.
   - Synthetic poorly-calibrated data → ECE > 0.10 before, < 0.05 after.
   - Out-of-bounds scores handled via clip.
   - Returned values are native `float`, not `np.float64`.
   - `extract_coefficients` → `msgspec.json.encode` → `msgspec.json.decode` →
     `load_coefficients` reproduces identical predictions.
2. `pytest atlas/ml/test_calibration_data.py -v` — all pass.
3. `pytest atlas/ml/test_reliability_diagram.py -v` — all pass.
   - SVG contains `<svg>` opening tag.
   - SVG contains expected number of `<circle>` elements (one per bin).
4. `pyright --pythonversion 3.12 atlas/ml/` — zero errors.
5. `grep -rn "import pickle\|import joblib\|import matplotlib" atlas/ml/calibrator.py atlas/ml/reliability_diagram.py` — zero.

## Anti-Pattern Checklist
- [ ] No bare `.fit()` or `.predict()` on async path — `asyncio.to_thread(...)`
- [ ] No `pickle` or `joblib` — serialise coefficients via `msgspec`
- [ ] No `matplotlib` — SVG generation is pure Python
- [ ] No `np.float64` in Pydantic models — cast to `float()`
- [ ] No `import aioredis` — `redis.asyncio`
- [ ] No `SQLAlchemy` — `asyncpg`
- [ ] No `os.getenv()` — `PolarisSettings`
- [ ] Coefficient round-trip: extract → msgspec encode → msgspec decode → load
      → identical predictions
- [ ] Cold-start fallback: if no trained model, calibrator returns `raw/100.0`
      (linear) — never raises
- [ ] All functions ≤ 40 lines
