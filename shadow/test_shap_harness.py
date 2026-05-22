"""Tests for shadow/shap_harness.py — S3-P12.

8 tests validating the SHAP Analysis Harness:
    1. < 200 records → InsufficientDataError raised with correct counts.
    2. 250 synthetic records → SHAPAnalysisReport returned without exception.
    3. Synthetic feature with strong correlation to y ranks composite_rank=1.
    4. composite_ranks contains all 5 features.
    5. high_confidence_candidates is a subset of promotion_candidates.
    6. Attempt to construct SHAPAnalysisReport with promotion_changes_scoring=True
       → Pydantic validation rejects at runtime.
    7. save_report creates both .txt and .json files at expected paths.
    8. Verify _run_shap_analysis is called via asyncio.to_thread.

All asyncpg calls are mocked via _load_data patch. numpy is real with
small synthetic datasets.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest
from pydantic import ValidationError

from shadow.shap_harness import (
    FeatureImportanceResult,
    InsufficientDataError,
    SHAPAnalysisHarness,
    SHAPAnalysisReport,
    _FEATURE_NAMES,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeRecord(dict):
    """Dict subclass mimicking asyncpg.Record key access."""

    def __getitem__(self, key: str) -> object:
        return super().__getitem__(key)


def _make_settings() -> MagicMock:
    """Create a mock PolarisSettings."""
    return MagicMock()


def _make_harness() -> SHAPAnalysisHarness:
    """Create SHAPAnalysisHarness with mock dependencies."""
    return SHAPAnalysisHarness(
        settings=_make_settings(),
        asyncpg_pool=AsyncMock(),
    )


def _make_synthetic_rows(
    n: int,
    correlated: bool = False,
) -> list[_FakeRecord]:
    """Generate synthetic data rows mimicking asyncpg.Record.

    If correlated=True, vwap_deviation is strongly correlated with
    outcome_pnl_pct, making it the dominant feature.
    """
    rng = np.random.default_rng(42)
    rows: list[_FakeRecord] = []
    for _ in range(n):
        vwap = rng.normal(0.0, 0.02)
        ob = rng.normal(0.0, 0.1)
        sr = rng.uniform(0.1, 2.0)
        atr = rng.uniform(0.5, 2.0)
        score = int(rng.integers(40, 180))

        if correlated:
            pnl = vwap * 50.0 + rng.normal(0.0, 0.01)
        else:
            pnl = rng.normal(0.0, 5.0)

        rows.append(_FakeRecord({
            "vwap_deviation": vwap,
            "ob_imbalance": ob,
            "sr_proximity": sr,
            "atr_move": atr,
            "original_score": score,
            "outcome_pnl_pct": pnl,
        }))
    return rows


# ---------------------------------------------------------------------------
# Test 1: Insufficient data raises InsufficientDataError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_insufficient_data_raises_error() -> None:
    """< 200 records → InsufficientDataError with correct counts."""
    harness = _make_harness()
    rows = _make_synthetic_rows(50)

    with patch.object(
        harness, "_load_data",
        new_callable=AsyncMock, return_value=rows,
    ):
        with pytest.raises(InsufficientDataError) as exc_info:
            await harness.run_analysis(min_trades=200)

    assert exc_info.value.current_count == 50
    assert exc_info.value.required_count == 200


# ---------------------------------------------------------------------------
# Test 2: 250 records → successful report
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_successful_analysis_250_records() -> None:
    """250 synthetic records → SHAPAnalysisReport returned."""
    harness = _make_harness()
    rows = _make_synthetic_rows(250)

    with patch.object(
        harness, "_load_data",
        new_callable=AsyncMock, return_value=rows,
    ):
        report = await harness.run_analysis(min_trades=200)

    assert isinstance(report, SHAPAnalysisReport)
    assert report.trades_analyzed == 250
    assert report.promotion_changes_scoring is False


# ---------------------------------------------------------------------------
# Test 3: Correlated feature ranks as composite_rank=1
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_correlated_feature_ranks_first() -> None:
    """Synthetic feature strongly correlated with y → composite_rank 1."""
    harness = _make_harness()
    rows = _make_synthetic_rows(300, correlated=True)

    with patch.object(
        harness, "_load_data",
        new_callable=AsyncMock, return_value=rows,
    ):
        report = await harness.run_analysis(min_trades=200)

    vwap_rank = report.composite_ranks["vwap_deviation"]
    other_ranks = [
        v for k, v in report.composite_ranks.items()
        if k != "vwap_deviation"
    ]
    assert vwap_rank <= min(other_ranks), (
        "vwap_deviation should be ranked highest with strong correlation"
    )


# ---------------------------------------------------------------------------
# Test 4: composite_ranks contains all 5 features
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_composite_ranks_contains_all_features() -> None:
    """composite_ranks dict has all 5 feature names."""
    harness = _make_harness()
    rows = _make_synthetic_rows(250)

    with patch.object(
        harness, "_load_data",
        new_callable=AsyncMock, return_value=rows,
    ):
        report = await harness.run_analysis(min_trades=200)

    assert set(report.composite_ranks.keys()) == set(_FEATURE_NAMES)


# ---------------------------------------------------------------------------
# Test 5: high_confidence_candidates ⊆ promotion_candidates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_high_confidence_subset_of_candidates() -> None:
    """high_confidence_candidates is a subset of promotion_candidates."""
    harness = _make_harness()
    rows = _make_synthetic_rows(300)

    with patch.object(
        harness, "_load_data",
        new_callable=AsyncMock, return_value=rows,
    ):
        report = await harness.run_analysis(min_trades=200)

    assert set(report.high_confidence_candidates).issubset(
        set(report.promotion_candidates),
    )


# ---------------------------------------------------------------------------
# Test 6: promotion_changes_scoring=True → validation error
# ---------------------------------------------------------------------------


def test_promotion_changes_scoring_true_rejected() -> None:
    """SHAPAnalysisReport with promotion_changes_scoring=True → error."""
    with pytest.raises(ValidationError):
        SHAPAnalysisReport(
            generated_at=datetime.now(timezone.utc),
            trades_analyzed=200,
            asset_filter=None,
            feature_importance={},
            composite_ranks={},
            high_confidence_candidates=[],
            promotion_candidates=[],
            report_text="test",
            promotion_changes_scoring=True,
        )


# ---------------------------------------------------------------------------
# Test 7: save_report creates .txt and .json files
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_report_creates_files(tmp_path: object) -> None:
    """save_report creates both .txt and .json files."""
    import pathlib

    output_dir = pathlib.Path(str(tmp_path))
    harness = _make_harness()

    report = SHAPAnalysisReport(
        generated_at=datetime(2026, 4, 26, tzinfo=timezone.utc),
        trades_analyzed=250,
        asset_filter=None,
        feature_importance={},
        composite_ranks={},
        high_confidence_candidates=[],
        promotion_candidates=[],
        report_text="Test report content",
    )

    await harness.save_report(report, output_dir)

    txt_file = output_dir / "shap_report_2026_04_26.txt"
    json_file = output_dir / "shap_report_2026_04_26.json"
    assert txt_file.exists()
    assert json_file.exists()
    assert txt_file.read_text(encoding="utf-8") == "Test report content"


# ---------------------------------------------------------------------------
# Test 8: asyncio.to_thread is used for ML calls
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_asyncio_to_thread_used_for_shap() -> None:
    """Verify all three ML methods are called via asyncio.to_thread."""
    harness = _make_harness()
    rows = _make_synthetic_rows(250)

    call_log: list[str] = []

    async def _tracking_to_thread(
        fn: object,
        *args: object,
        **kwargs: object,
    ) -> object:
        """Track which functions are dispatched to asyncio.to_thread."""
        call_log.append(getattr(fn, "__name__", str(fn)))
        return fn(*args, **kwargs)  # type: ignore[operator]

    with (
        patch.object(
            harness, "_load_data",
            new_callable=AsyncMock, return_value=rows,
        ),
        patch(
            "shadow.shap_harness.asyncio.to_thread",
            _tracking_to_thread,
        ),
    ):
        await harness.run_analysis(min_trades=200)

    assert "_run_shap_analysis" in call_log
    assert "_run_gini_analysis" in call_log
    assert "_run_permutation_analysis" in call_log


# ---------------------------------------------------------------------------
# Anti-pattern sentinel tests (ban verification)
# ---------------------------------------------------------------------------


def test_no_banned_imports_in_shap_harness() -> None:
    """Verify shap_harness.py contains no banned library imports."""
    import pathlib

    source = pathlib.Path(__file__).parent / "shap_harness.py"
    content = source.read_text(encoding="utf-8")

    banned = [
        "import polars",
        "from polars",
        "import pandas",
        "from pandas",
        "train_test_split",
        "KFold",
        "import pickle",
        "import joblib",
    ]
    for pattern in banned:
        assert pattern not in content, (
            "Banned pattern '{}' found in shap_harness.py".format(pattern)
        )

    # Positive check: asyncio.to_thread must appear at least 3 times
    assert content.count("asyncio.to_thread") >= 3, (
        "Expected at least 3 asyncio.to_thread calls"
    )
