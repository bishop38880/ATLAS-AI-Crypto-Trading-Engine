"""Meta-Learner for optimal ConfluenceScorer weights.

Uses a LogisticRegressionCV model trained via TimeSeriesSplit
to assign learned coefficients to agent outputs based on historical
profitability. This replaces fixed categorical weights.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import asyncpg
import msgspec
import numpy as np
from loguru import logger
from sklearn.linear_model import LogisticRegressionCV
from sklearn.model_selection import TimeSeriesSplit


class InsufficientDataError(Exception):
    """Raised when there are not enough samples to train the meta-learner."""

    pass


class StackingMetaLearner:
    """Trains and predicts using a logistic regression model.

    Learns coefficients for agent outputs (treated as features) based
    on historical profitability, using TimeSeriesSplit to prevent leakage.
    """

    MODEL_PATH = Path("atlas/ml/models/meta_learner_coefficients.json")

    def __init__(self, asyncpg_pool: asyncpg.Pool) -> None:
        """Initialize the meta learner with an asyncpg pool."""
        self._pool = asyncpg_pool
        self.model: LogisticRegressionCV | None = None
        self._load_model()

    def _load_model(self) -> None:
        """Load stored coefficients and reconstruct LogisticRegressionCV."""
        if not self.MODEL_PATH.exists():
            return

        try:
            data = self.MODEL_PATH.read_bytes()
            coeffs = msgspec.json.decode(data)

            self.model = LogisticRegressionCV()
            self.model.coef_ = np.array(coeffs["coef"])
            self.model.intercept_ = np.array(coeffs["intercept"])
            self.model.classes_ = np.array(coeffs["classes"])
            logger.info("Meta-learner reconstructed from stored coefficients.")
        except Exception as e:
            logger.warning("Failed to load meta-learner model: {}", e)
            self.model = None

    async def build_dataset(
        self, min_samples: int = 200
    ) -> tuple[np.ndarray, np.ndarray]:
        """Query Postgres for historical signal records.

        Builds X (10 agent scores) and y (1 if profitable else 0).
        Raises InsufficientDataError if count < min_samples.
        """
        rows = await self._query_signal_history()

        if len(rows) < min_samples:
            raise InsufficientDataError(
                f"Found {len(rows)} samples, need {min_samples}"
            )

        return self._parse_feature_rows(rows)

    async def _query_signal_history(self) -> list[asyncpg.Record]:
        """Fetch joined signal history and trade data."""
        async with self._pool.acquire() as conn:
            return await conn.fetch(
                """
                SELECT s.agent_breakdown, t.pnl_pct
                FROM signal_history s
                JOIN trade_signals t ON s.signal_id = t.signal_id
                WHERE t.pnl_pct IS NOT NULL
                """,
                timeout=10.0,
            )

    def _parse_feature_rows(
        self, rows: list[asyncpg.Record],
    ) -> tuple[np.ndarray, np.ndarray]:
        """Parse DB rows into feature matrix X and label vector y."""
        x_list: list[list[float]] = []
        y_list: list[int] = []
        for row in rows:
            breakdown = row["agent_breakdown"]
            if isinstance(breakdown, str):
                breakdown = msgspec.json.decode(breakdown)

            pnl = Decimal(str(row["pnl_pct"]))
            y_list.append(1 if pnl > Decimal("0") else 0)

            sorted_agents = sorted(breakdown.items(), key=lambda x: x[0])
            scores = [float(v.get("score", 0)) for _, v in sorted_agents]

            if len(scores) < 10:
                scores += [0.0] * (10 - len(scores))
            elif len(scores) > 10:
                scores = scores[:10]

            x_list.append(scores)

        return np.array(x_list), np.array(y_list)

    def train(self, X: np.ndarray, y: np.ndarray) -> None:
        """Train the model using TimeSeriesSplit to prevent data leakage."""
        cv = TimeSeriesSplit(n_splits=5)
        self.model = LogisticRegressionCV(cv=cv)
        self.model.fit(X, y)

        coeffs = {
            "coef": np.asarray(self.model.coef_).tolist(),
            "intercept": np.asarray(self.model.intercept_).tolist(),
            "classes": np.asarray(self.model.classes_).tolist(),
        }

        self.MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.MODEL_PATH.write_bytes(msgspec.json.encode(coeffs))
        logger.info("Meta-learner trained and coefficients stored.")

    def predict(self, scores: list[float]) -> float:
        """Predict probability of profitability based on agent scores.
        
        Returns native Python float, not numpy float64.
        """
        if self.model is None:
            raise ValueError("Model not loaded or trained")

        if len(scores) < 10:
            scores += [0.0] * (10 - len(scores))
        elif len(scores) > 10:
            scores = scores[:10]

        x_val = np.array([scores])
        prob = self.model.predict_proba(x_val)[0]

        # predict_proba returns an array aligning with classes_
        idx = np.where(self.model.classes_ == 1)[0]
        if len(idx) > 0:
            return float(prob[idx[0]])

        return 0.0
