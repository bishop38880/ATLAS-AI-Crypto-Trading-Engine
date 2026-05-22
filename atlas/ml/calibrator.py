import msgspec
import numpy as np
from datetime import datetime, timezone
from sklearn.isotonic import IsotonicRegression
from pydantic import BaseModel


class CalibratorCoefficients(msgspec.Struct, frozen=True):
    """Serialisable coefficient bundle — fully round-trippable."""
    x_thresholds: list[float]
    y_thresholds: list[float]
    out_of_bounds: str
    trained_at_iso: str
    training_samples: int
    ece: float = 0.0


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
        self.last_ece: float = 0.0

    def train(self, raw_scores: np.ndarray, outcomes: np.ndarray) -> CalibrationResult:
        """Synchronous — caller MUST wrap in asyncio.to_thread."""
        self._model.fit(raw_scores, outcomes)
        self._fitted = True
        ece, bins = self._compute_ece(raw_scores, outcomes, n_bins=10)
        self.last_ece = float(ece)
        return CalibrationResult(
            ece=float(ece), 
            bins=bins, 
            n_samples=len(raw_scores)
        )

    def _compute_ece(self, raw_scores: np.ndarray, outcomes: np.ndarray, n_bins: int) -> tuple[float, list[CalibrationBin]]:
        predicted = self._model.predict(raw_scores)
        bins = np.linspace(0.0, 1.0, n_bins + 1)
        
        ece = 0.0
        n_total = len(raw_scores)
        bin_results = []
        
        for i in range(n_bins):
            bin_lower = float(bins[i])
            bin_upper = float(bins[i + 1])
            
            if i == n_bins - 1:
                in_bin = (predicted >= bin_lower) & (predicted <= bin_upper)
            else:
                in_bin = (predicted >= bin_lower) & (predicted < bin_upper)
                
            n_samples = int(np.sum(in_bin))
            if n_samples == 0:
                bin_results.append(CalibrationBin(
                    bin_lower=bin_lower, bin_upper=bin_upper,
                    predicted_mean=0.0, observed_rate=0.0, n_samples=0
                ))
                continue
                
            pred_mean = float(np.mean(predicted[in_bin]))
            obs_rate = float(np.mean(outcomes[in_bin]))
            
            ece += (n_samples / n_total) * abs(pred_mean - obs_rate)
            bin_results.append(CalibrationBin(
                bin_lower=bin_lower, bin_upper=bin_upper,
                predicted_mean=pred_mean, observed_rate=obs_rate, n_samples=n_samples
            ))
            
        return float(ece), bin_results

    def calibrate(self, raw_score: float) -> float:
        """Synchronous — caller MUST wrap in asyncio.to_thread."""
        if not self._fitted:
            return float(raw_score) / 100.0
        return float(self._model.predict([raw_score])[0])

    def extract_coefficients(self) -> CalibratorCoefficients:
        """Returns msgspec-serialisable bundle for persistence."""
        if not self._fitted:
            raise RuntimeError("Model is not fitted")
        return CalibratorCoefficients(
            x_thresholds=[float(x) for x in self._model.X_thresholds_],
            y_thresholds=[float(y) for y in self._model.y_thresholds_],
            out_of_bounds="clip",
            trained_at_iso=datetime.now(timezone.utc).isoformat(),
            training_samples=int(self._model.X_thresholds_.shape[0]),
            ece=self.last_ece,
        )

    def load_coefficients(self, coeffs: CalibratorCoefficients) -> None:
        """Reconstruct from serialised coefficients."""
        self._model = IsotonicRegression(out_of_bounds=coeffs.out_of_bounds)
        self._model.X_thresholds_ = np.asarray(coeffs.x_thresholds, dtype=np.float64)
        self._model.y_thresholds_ = np.asarray(coeffs.y_thresholds, dtype=np.float64)
        
        if len(self._model.X_thresholds_) > 0:
            self._model.X_min_ = self._model.X_thresholds_[0]
            self._model.X_max_ = self._model.X_thresholds_[-1]
            
            from scipy.interpolate import interp1d
            self._model.f_ = interp1d(
                self._model.X_thresholds_, 
                self._model.y_thresholds_, 
                kind='linear',
                bounds_error=False,
                fill_value="extrapolate"  # type: ignore
            )
        else:
            self._model.X_min_ = 0.0
            self._model.X_max_ = 100.0
            
        self._fitted = True
        self.last_ece = coeffs.ece
