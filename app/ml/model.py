"""
Phase 5: the swappable points-prediction model interface.

`PointsPredictorModel` is the seam the project prompt asks for: "the
prediction model should initially use a Random Forest Regressor but be
designed so different models (e.g. XGBoost) can be swapped in later."
`RandomForestPointsPredictor` is today's implementation; a future
`XGBoostPointsPredictor` (or a small neural net, per the project's
"Future Features" list) only needs to implement the same four methods
and can be dropped into `DelphiPredictionEngine` with a one-line change.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from loguru import logger
from sklearn.ensemble import RandomForestRegressor

from app.ml.features import FEATURE_NAMES


class PointsPredictorModel(ABC):
    """Common interface every DELPHI model implementation must satisfy."""

    #: Short identifier used in `Prediction.model_name` / file names.
    name: str = "base"

    @abstractmethod
    def fit(self, X: np.ndarray, y: np.ndarray) -> None:
        """Train the model in place on feature matrix `X` and targets `y`."""

    @abstractmethod
    def predict(self, X: np.ndarray) -> np.ndarray:
        """Predict expected points for each row of `X`."""

    @abstractmethod
    def feature_importances(self) -> dict[str, float]:
        """Return a {feature_name: importance} mapping for explainability."""

    @abstractmethod
    def save(self, directory: Path) -> Path:
        """Persist the model to `directory`, returning the artifact path."""

    @abstractmethod
    def load(self, path: Path) -> None:
        """Load model state from a previously-saved artifact."""


@dataclass
class TrainingMetrics:
    """Offline evaluation metrics from a train/test split."""

    mae: float
    rmse: float
    r2: float
    n_train: int
    n_test: int
    trained_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "mae": self.mae,
            "rmse": self.rmse,
            "r2": self.r2,
            "n_train": self.n_train,
            "n_test": self.n_test,
            "trained_at": self.trained_at.isoformat(),
        }


class RandomForestPointsPredictor(PointsPredictorModel):
    """DELPHI's default model: a scikit-learn Random Forest Regressor.

    Chosen over a linear model because FPL points are a genuinely
    non-linear function of the inputs (e.g. clean-sheet points only
    accrue above an ~60-minute threshold, fixture difficulty interacts
    with position), and over a small neural net because the training set
    size early in a project's life (one season, a few hundred players) is
    far too small to justify anything hungrier for data. Robust to
    unscaled/mixed-magnitude features, so no separate scaling step is
    needed upstream.
    """

    name = "delphi_rf"

    def __init__(
        self,
        n_estimators: int = 300,
        max_depth: int | None = 10,
        min_samples_leaf: int = 3,
        random_state: int = 42,
        version: str = "1.0.0",
    ) -> None:
        self.version = version
        self._params = dict(
            n_estimators=n_estimators,
            max_depth=max_depth,
            min_samples_leaf=min_samples_leaf,
            random_state=random_state,
            n_jobs=-1,
        )
        self._regressor: RandomForestRegressor = RandomForestRegressor(**self._params)
        self._is_fitted = False

    @property
    def is_fitted(self) -> bool:
        return self._is_fitted

    def fit(self, X: np.ndarray, y: np.ndarray) -> None:
        self._regressor.fit(X, y)
        self._is_fitted = True
        logger.info(
            "RandomForestPointsPredictor fitted on {} samples, {} features",
            X.shape[0],
            X.shape[1],
        )

    def predict(self, X: np.ndarray) -> np.ndarray:
        if not self._is_fitted:
            raise RuntimeError(
                "RandomForestPointsPredictor.predict called before fit()/load()"
            )
        # Never predict negative points - FPL scores don't go below 0
        # once you floor at "didn't play", and small negative predictions
        # for very low-minutes players are a known RF edge artifact.
        return np.clip(self._regressor.predict(X), a_min=0.0, a_max=None)

    def feature_importances(self) -> dict[str, float]:
        if not self._is_fitted:
            return {}
        importances = self._regressor.feature_importances_
        return dict(
            sorted(
                zip(FEATURE_NAMES, (float(v) for v in importances)),
                key=lambda item: item[1],
                reverse=True,
            )
        )

    def save(self, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        artifact_path = directory / f"{self.name}_v{self.version}.joblib"
        joblib.dump(
            {
                "regressor": self._regressor,
                "params": self._params,
                "version": self.version,
                "feature_names": FEATURE_NAMES,
            },
            artifact_path,
        )

        metadata_path = directory / f"{self.name}_v{self.version}.meta.json"
        metadata_path.write_text(
            json.dumps(
                {
                    "name": self.name,
                    "version": self.version,
                    "params": self._params,
                    "saved_at": datetime.now(timezone.utc).isoformat(),
                    "feature_importances": self.feature_importances(),
                },
                indent=2,
            )
        )
        logger.info("Saved {} to {}", self.name, artifact_path)
        return artifact_path

    def load(self, path: Path) -> None:
        if not path.exists():
            raise FileNotFoundError(f"No model artifact at {path}")
        payload = joblib.load(path)

        stored_features = tuple(payload.get("feature_names", FEATURE_NAMES))
        if stored_features != FEATURE_NAMES:
            raise ValueError(
                "Saved model's feature schema does not match the current "
                "FEATURE_NAMES - retrain before using this artifact."
            )

        self._regressor = payload["regressor"]
        self._params = payload.get("params", self._params)
        self.version = payload.get("version", self.version)
        self._is_fitted = True
        logger.info("Loaded {} from {}", self.name, path)

    @classmethod
    def latest_artifact_path(cls, directory: Path) -> Path | None:
        """Return the most recently modified saved artifact, if any."""
        if not directory.exists():
            return None
        candidates = sorted(
            directory.glob(f"{cls.name}_v*.joblib"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        return candidates[0] if candidates else None
