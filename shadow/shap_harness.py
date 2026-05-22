"""SHAP Analysis Harness — S3-P12.

Process accumulated ``shadow_alpha_tracking`` data through SHAP, Gini,
and Permutation importance to produce an evidence report for human
review at the 200-trade milestone.

**No promotion logic.** Analysis only. READ-ONLY.

Architecture:
    - All ML library calls (sklearn, xgboost, shap) wrapped in
      ``asyncio.to_thread()`` — they are synchronous CPU-bound.
    - No pandas, no polars — pure numpy for feature/target arrays.
    - Frozen Pydantic v2 models for all outputs.
    - Loguru structured kwargs only.
    - 40-line function limit enforced.
    - ``promotion_changes_scoring`` is a hard constant (False).
    - ``TimeSeriesSplit`` only for any cross-validation.
"""

from __future__ import annotations

import asyncio
import pathlib
from datetime import datetime, timezone

import asyncpg
import msgspec
import numpy as np

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, field_validator

from atlas.shared.config import PolarisSettings


# ---------------------------------------------------------------------------
# Feature column ordering — canonical across all three methods
# ---------------------------------------------------------------------------

_FEATURE_NAMES: tuple[str, ...] = (
    "vwap_deviation",
    "ob_imbalance",
    "sr_proximity",
    "atr_move",
    "original_score",
)


# ---------------------------------------------------------------------------
# Frozen Pydantic models
# ---------------------------------------------------------------------------


class FeatureImportanceResult(BaseModel, frozen=True):
    """Importance scores for a single feature across all three methods."""

    model_config = ConfigDict(frozen=True)

    feature_name: str
    shap_mean_abs: float
    shap_rank: int
    gini_importance: float
    gini_rank: int
    permutation_importance_mean: float
    permutation_importance_std: float
    permutation_rank: int
    composite_avg_rank: float


class SHAPAnalysisReport(BaseModel, frozen=True):
    """Structured evidence document for human review.

    ``promotion_changes_scoring`` is a hard constant — always False.
    """

    model_config = ConfigDict(frozen=True)

    generated_at: datetime
    trades_analyzed: int
    asset_filter: str | None
    feature_importance: dict[str, FeatureImportanceResult]
    composite_ranks: dict[str, float]
    high_confidence_candidates: list[str]
    promotion_candidates: list[str]
    report_text: str
    promotion_changes_scoring: bool = Field(default=False)

    @field_validator("promotion_changes_scoring")
    @classmethod
    def _must_be_false(cls, v: bool) -> bool:
        """Hard invariant: SHAP harness never changes scoring."""
        if v is True:
            raise ValueError(
                "promotion_changes_scoring must always be False"
            )
        return v


# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------


class InsufficientDataError(Exception):
    """Raised when too few trade records exist for analysis."""

    def __init__(self, current_count: int, required_count: int) -> None:
        self.current_count = current_count
        self.required_count = required_count
        super().__init__(
            "Insufficient data: have {}, need {}".format(
                current_count, required_count,
            ),
        )


# ---------------------------------------------------------------------------
# SQL queries
# ---------------------------------------------------------------------------

_LOAD_DATA_SQL = """
    SELECT
        sm.vwap_deviation,
        sm.ob_imbalance,
        sm.sr_proximity,
        sm.atr_move,
        sat.original_score,
        sat.outcome_pnl_pct
    FROM shadow_metrics sm
    JOIN shadow_alpha_tracking sat
        ON sm.asset = sat.asset
    WHERE sat.outcome_pnl_pct IS NOT NULL
"""

_LOAD_DATA_SQL_FILTERED = """
    SELECT
        sm.vwap_deviation,
        sm.ob_imbalance,
        sm.sr_proximity,
        sm.atr_move,
        sat.original_score,
        sat.outcome_pnl_pct
    FROM shadow_metrics sm
    JOIN shadow_alpha_tracking sat
        ON sm.asset = sat.asset
    WHERE sat.outcome_pnl_pct IS NOT NULL
      AND sat.asset = $1
"""


# ---------------------------------------------------------------------------
# SHAPAnalysisHarness
# ---------------------------------------------------------------------------


class SHAPAnalysisHarness:
    """Run SHAP, Gini, and Permutation analysis on shadow data.

    Produces a structured evidence report. Does NOT make promotion
    decisions. Does NOT modify scoring config. READ-ONLY analysis.
    """

    def __init__(
        self,
        settings: PolarisSettings,
        asyncpg_pool: asyncpg.Pool,
    ) -> None:
        self._settings = settings
        self._pool = asyncpg_pool

    # ── Core API ─────────────────────────────────────────────────────

    async def run_analysis(
        self,
        min_trades: int = 200,
        asset_filter: str | None = None,
    ) -> SHAPAnalysisReport:
        """Execute the full analysis pipeline.

        Raises InsufficientDataError if < min_trades rows found.
        """
        rows = await self._load_data(asset_filter)
        if len(rows) < min_trades:
            raise InsufficientDataError(len(rows), min_trades)

        x_arr, y_arr = _build_numpy_arrays(rows)

        shap_results, gini_results, perm_results = await asyncio.gather(
            asyncio.to_thread(self._run_shap_analysis, x_arr, y_arr),
            asyncio.to_thread(self._run_gini_analysis, x_arr, y_arr),
            asyncio.to_thread(
                self._run_permutation_analysis, x_arr, y_arr,
            ),
        )

        composite = _compute_composite_ranks(
            shap_results, gini_results, perm_results,
        )

        report = _assemble_report(
            shap_results, gini_results, perm_results,
            composite, len(rows), asset_filter,
        )

        _log_analysis_complete(report, asset_filter)

        return report

    # ── Data Loading ─────────────────────────────────────────────────

    async def _load_data(
        self,
        asset_filter: str | None,
    ) -> list[asyncpg.Record]:
        """Fetch joined shadow_metrics + alpha_tracking rows."""
        async with self._pool.acquire() as conn:
            if asset_filter is not None:
                return await asyncio.wait_for(
                    conn.fetch(_LOAD_DATA_SQL_FILTERED, asset_filter),
                    timeout=30.0,
                )
            return await asyncio.wait_for(
                conn.fetch(_LOAD_DATA_SQL),
                timeout=30.0,
            )

    # ── SHAP Analysis (synchronous — called via to_thread) ───────────

    def _run_shap_analysis(
        self,
        x_arr: np.ndarray,
        y_arr: np.ndarray,
    ) -> dict[str, float]:
        """Train XGBRegressor + TreeExplainer → mean |SHAP| per feature."""
        import shap
        import xgboost as xgb

        model = xgb.XGBRegressor(
            n_estimators=100,
            random_state=42,
        )
        model.fit(x_arr, y_arr)

        explainer = shap.TreeExplainer(model)
        shap_values = explainer.shap_values(x_arr)

        return _shap_mean_abs(shap_values)

    # ── Gini Analysis (synchronous — called via to_thread) ───────────

    def _run_gini_analysis(
        self,
        x_arr: np.ndarray,
        y_arr: np.ndarray,
    ) -> dict[str, float]:
        """RandomForestRegressor → Gini importances per feature."""
        from sklearn.ensemble import RandomForestRegressor

        model = RandomForestRegressor(
            n_estimators=100,
            random_state=42,
        )
        model.fit(x_arr, y_arr)

        return {
            _FEATURE_NAMES[i]: float(model.feature_importances_[i])
            for i in range(len(_FEATURE_NAMES))
        }

    # ── Permutation Analysis (synchronous — called via to_thread) ────

    def _run_permutation_analysis(
        self,
        x_arr: np.ndarray,
        y_arr: np.ndarray,
    ) -> dict[str, tuple[float, float]]:
        """Permutation importance on RandomForestRegressor."""
        from sklearn.ensemble import RandomForestRegressor
        from sklearn.inspection import permutation_importance

        model = RandomForestRegressor(
            n_estimators=100,
            random_state=42,
        )
        model.fit(x_arr, y_arr)

        result = permutation_importance(
            model, x_arr, y_arr,
            n_repeats=30,
            random_state=42,
        )

        return _perm_results_to_dict(result)

    # ── Report Persistence ───────────────────────────────────────────

    async def save_report(
        self,
        report: SHAPAnalysisReport,
        output_path: pathlib.Path,
    ) -> None:
        """Save report as .txt and .json to output_path."""
        output_path.mkdir(parents=True, exist_ok=True)
        date_str = report.generated_at.strftime("%Y_%m_%d")
        txt_name = "shap_report_{}.txt".format(date_str)
        json_name = "shap_report_{}.json".format(date_str)

        await asyncio.to_thread(
            _write_txt, output_path / txt_name, report.report_text,
        )
        await asyncio.to_thread(
            _write_json, output_path / json_name, report,
        )

        logger.info(
            "shap_report_saved | txt_path={} | json_path={}",
            str(output_path / txt_name),
            str(output_path / json_name),
        )


# ---------------------------------------------------------------------------
# Pure helper functions — extracted for 40-line cap
# ---------------------------------------------------------------------------


def _log_analysis_complete(
    report: SHAPAnalysisReport,
    asset_filter: str | None,
) -> None:
    """Log completion and assert the hard invariant."""
    assert not report.promotion_changes_scoring, (
        "SHAP harness must never declare scoring changes"
    )
    logger.info(
        "shap_analysis_complete | trades_analyzed={} | asset_filter={} | candidates={}",
        report.trades_analyzed,
        asset_filter,
        len(report.promotion_candidates),
    )


def _build_numpy_arrays(
    rows: list[asyncpg.Record],
) -> tuple[np.ndarray, np.ndarray]:
    """Convert asyncpg rows to numpy X (n×5) and y (n,) arrays."""
    n = len(rows)
    x_arr = np.empty((n, len(_FEATURE_NAMES)), dtype=np.float64)
    y_arr = np.empty(n, dtype=np.float64)

    for i, row in enumerate(rows):
        x_arr[i, 0] = float(row["vwap_deviation"] or 0.0)
        x_arr[i, 1] = float(row["ob_imbalance"] or 0.0)
        x_arr[i, 2] = float(row["sr_proximity"] or 0.0)
        x_arr[i, 3] = float(row["atr_move"] or 0.0)
        x_arr[i, 4] = float(row["original_score"] or 0)
        y_arr[i] = float(row["outcome_pnl_pct"] or 0.0)

    return x_arr, y_arr


def _shap_mean_abs(shap_values: np.ndarray) -> dict[str, float]:
    """Compute mean absolute SHAP value per feature."""
    mean_abs = np.mean(np.abs(shap_values), axis=0)
    return {
        _FEATURE_NAMES[i]: float(mean_abs[i])
        for i in range(len(_FEATURE_NAMES))
    }


def _perm_results_to_dict(
    result: object,
) -> dict[str, tuple[float, float]]:
    """Extract (mean, std) per feature from permutation result."""
    return {
        _FEATURE_NAMES[i]: (
            float(result.importances_mean[i]),  # type: ignore[union-attr]
            float(result.importances_std[i]),  # type: ignore[union-attr]
        )
        for i in range(len(_FEATURE_NAMES))
    }


def _rank_dict(values: dict[str, float]) -> dict[str, int]:
    """Rank features by importance (1 = highest value)."""
    sorted_feats = sorted(values, key=values.get, reverse=True)  # type: ignore[arg-type]
    return {feat: rank + 1 for rank, feat in enumerate(sorted_feats)}


def _compute_composite_ranks(
    shap_results: dict[str, float],
    gini_results: dict[str, float],
    perm_results: dict[str, tuple[float, float]],
) -> dict[str, float]:
    """Compute average rank across all three methods."""
    shap_ranks = _rank_dict(shap_results)
    gini_ranks = _rank_dict(gini_results)
    perm_means = {k: v[0] for k, v in perm_results.items()}
    perm_ranks = _rank_dict(perm_means)

    return {
        feat: (shap_ranks[feat] + gini_ranks[feat] + perm_ranks[feat]) / 3.0
        for feat in _FEATURE_NAMES
    }


def _assemble_report(
    shap_results: dict[str, float],
    gini_results: dict[str, float],
    perm_results: dict[str, tuple[float, float]],
    composite: dict[str, float],
    trades_analyzed: int,
    asset_filter: str | None,
) -> SHAPAnalysisReport:
    """Build the full SHAPAnalysisReport."""
    shap_ranks = _rank_dict(shap_results)
    gini_ranks = _rank_dict(gini_results)
    perm_means = {k: v[0] for k, v in perm_results.items()}
    perm_ranks = _rank_dict(perm_means)

    importance = _build_feature_importance(
        shap_results, gini_results, perm_results,
        shap_ranks, gini_ranks, perm_ranks, composite,
    )

    candidates = _identify_candidates(composite)
    high_conf = _identify_high_confidence(composite)

    report_text = _build_report_text_from_parts(
        trades_analyzed, asset_filter, importance,
        candidates, high_conf,
    )

    return SHAPAnalysisReport(
        generated_at=datetime.now(timezone.utc),
        trades_analyzed=trades_analyzed,
        asset_filter=asset_filter,
        feature_importance=importance,
        composite_ranks=composite,
        high_confidence_candidates=high_conf,
        promotion_candidates=candidates,
        report_text=report_text,
    )


def _build_feature_importance(
    shap_r: dict[str, float],
    gini_r: dict[str, float],
    perm_r: dict[str, tuple[float, float]],
    shap_ranks: dict[str, int],
    gini_ranks: dict[str, int],
    perm_ranks: dict[str, int],
    composite: dict[str, float],
) -> dict[str, FeatureImportanceResult]:
    """Build per-feature importance results."""
    result: dict[str, FeatureImportanceResult] = {}
    for feat in _FEATURE_NAMES:
        result[feat] = FeatureImportanceResult(
            feature_name=feat,
            shap_mean_abs=shap_r[feat],
            shap_rank=shap_ranks[feat],
            gini_importance=gini_r[feat],
            gini_rank=gini_ranks[feat],
            permutation_importance_mean=perm_r[feat][0],
            permutation_importance_std=perm_r[feat][1],
            permutation_rank=perm_ranks[feat],
            composite_avg_rank=composite[feat],
        )
    return result


def _identify_candidates(
    composite: dict[str, float],
) -> list[str]:
    """Features with avg_rank <= 4 are promotion candidates."""
    return [f for f, r in composite.items() if r <= 4.0]


def _identify_high_confidence(
    composite: dict[str, float],
) -> list[str]:
    """Features with avg_rank <= 2 are high-confidence candidates."""
    return [f for f, r in composite.items() if r <= 2.0]


def _build_report_text_from_parts(
    trades_analyzed: int,
    asset_filter: str | None,
    importance: dict[str, FeatureImportanceResult],
    candidates: list[str],
    high_conf: list[str],
) -> str:
    """Build plain-text human-readable summary."""
    lines: list[str] = [
        "=" * 60,
        "SHAP ANALYSIS REPORT",
        "=" * 60,
        "Trades analyzed: {}".format(trades_analyzed),
        "Asset filter: {}".format(asset_filter or "ALL"),
        "",
        "--- Feature Importance Rankings ---",
    ]
    _append_feature_lines(lines, importance)
    _append_candidate_lines(lines, candidates, high_conf)
    _append_disclaimer(lines)
    return "\n".join(lines)


def _append_feature_lines(
    lines: list[str],
    importance: dict[str, FeatureImportanceResult],
) -> None:
    """Append per-feature detail lines to the report."""
    for feat, imp in importance.items():
        lines.append(
            "  {} | SHAP={:.4f} (#{}) | Gini={:.4f} (#{}) "
            "| Perm={:.4f}±{:.4f} (#{}) | Avg Rank={:.2f}".format(
                feat, imp.shap_mean_abs, imp.shap_rank,
                imp.gini_importance, imp.gini_rank,
                imp.permutation_importance_mean,
                imp.permutation_importance_std,
                imp.permutation_rank, imp.composite_avg_rank,
            ),
        )


def _append_candidate_lines(
    lines: list[str],
    candidates: list[str],
    high_conf: list[str],
) -> None:
    """Append candidate summary to the report."""
    lines.append("")
    lines.append("--- Promotion Candidates (avg_rank <= 4) ---")
    for c in candidates:
        lines.append("  - {}".format(c))
    lines.append("")
    lines.append("--- High Confidence (avg_rank <= 2) ---")
    for c in high_conf:
        lines.append("  - {}".format(c))


def _append_disclaimer(lines: list[str]) -> None:
    """Append the mandatory disclaimer text."""
    lines.extend([
        "",
        "=" * 60,
        "NOTE: This report is for human review only. No scoring "
        "changes have been made.",
        "To promote a metric: update POLARIS Context Document "
        "Section 9, then update",
        "PolarisSettings + scoring config explicitly. Manual "
        "change required.",
        "=" * 60,
    ])


def _write_txt(path: pathlib.Path, text: str) -> None:
    """Write text content to file (synchronous)."""
    path.write_text(text, encoding="utf-8")


def _write_json(path: pathlib.Path, report: SHAPAnalysisReport) -> None:
    """Write JSON content to file (synchronous)."""
    data = report.model_dump(mode="json")
    encoded = msgspec.json.encode(data)
    path.write_bytes(encoded)
