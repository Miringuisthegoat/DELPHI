"""
Phase 5: `ModelTrainingService` - turns accumulated gameweek history into a
trained DELPHI model.

This is deliberately separate from `DelphiPredictionEngine` (which
*uses* a model to produce weekly recommendations): training is a
periodic, relatively expensive batch job (run after each gameweek's
results are in, or on demand via `POST /predictions/train`), while
generating predictions is a fast, frequent read path.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
from loguru import logger
from sklearn.model_selection import train_test_split
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import PredictionError
from app.ml.features import FEATURE_NAMES, PlayerFeatureBuilder
from app.ml.model import RandomForestPointsPredictor, TrainingMetrics
from app.models.player import Player
from app.models.player_stats import PlayerGameweekStats


@dataclass
class TrainingResult:
    """Everything the API/CLI needs to report back after a training run."""

    metrics: TrainingMetrics
    model_path: str
    feature_importances: dict[str, float]


class ModelTrainingService:
    """Builds the training set from the database and fits `RandomForestPointsPredictor`."""

    def __init__(self, feature_builder: PlayerFeatureBuilder | None = None) -> None:
        self._feature_builder = feature_builder or PlayerFeatureBuilder()

    def build_training_data(self, db: Session) -> tuple[np.ndarray, np.ndarray]:
        """Build (X, y) from every historical (player, gameweek) result.

        For every `PlayerGameweekStats` row (an actual, already-played
        gameweek), the feature vector is reconstructed exactly as it would
        have looked *before* that gameweek was played (see
        `PlayerFeatureBuilder`'s no-lookahead rule), and the row's actual
        `total_points` becomes the training label. Rows with zero prior
        history (a player's very first recorded gameweek) are skipped -
        with nothing to compute rolling features from, they'd all be
        default/neutral values and would just teach the model noise.
        """
        rows = (
            db.execute(
                select(PlayerGameweekStats).order_by(
                    PlayerGameweekStats.player_id, PlayerGameweekStats.gameweek
                )
            )
            .scalars()
            .all()
        )

        features: list[list[float]] = []
        targets: list[float] = []

        # Cache players since the same player appears across many rows.
        player_cache: dict[int, Player | None] = {}

        for row in rows:
            player = player_cache.get(row.player_id)
            if player is None and row.player_id not in player_cache:
                player = db.get(Player, row.player_id)
                player_cache[row.player_id] = player
            if player is None:
                continue

            vector = self._feature_builder.build(db, player, row.gameweek)
            if not vector.has_history:
                continue

            features.append(vector.to_row())
            targets.append(float(row.total_points))

        if not features:
            return np.empty((0, len(FEATURE_NAMES))), np.empty((0,))

        return np.array(features, dtype=float), np.array(targets, dtype=float)

    def train(self, db: Session) -> TrainingResult:
        """Build the training set, fit the model, evaluate, and persist it.

        Raises:
            PredictionError: if fewer than
                `settings.ml_min_samples_for_training` labelled rows are
                available - training on too little data would silently
                produce a model less reliable than the heuristic
                predictor it's meant to replace.
        """
        X, y = self.build_training_data(db)

        if X.shape[0] < settings.ml_min_samples_for_training:
            raise PredictionError(
                f"Only {X.shape[0]} labelled training rows are available "
                f"(need at least {settings.ml_min_samples_for_training}). "
                "This is expected in preseason or the first few gameweeks "
                "of a season - DELPHI will keep using the heuristic "
                "predictor until enough gameweek history accumulates."
            )

        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=settings.ml_random_state
        )

        model = RandomForestPointsPredictor(
            n_estimators=settings.ml_rf_n_estimators,
            max_depth=settings.ml_rf_max_depth,
            min_samples_leaf=settings.ml_rf_min_samples_leaf,
            random_state=settings.ml_random_state,
            version=settings.ml_model_version,
        )
        model.fit(X_train, y_train)

        predictions = model.predict(X_test)
        metrics = self._evaluate(y_test, predictions, len(X_train), len(X_test))

        artifact_path = model.save(settings.ml_model_dir)

        logger.info(
            "DELPHI training complete: MAE={:.2f} RMSE={:.2f} R2={:.2f} "
            "(train={}, test={})",
            metrics.mae,
            metrics.rmse,
            metrics.r2,
            metrics.n_train,
            metrics.n_test,
        )

        return TrainingResult(
            metrics=metrics,
            model_path=str(artifact_path),
            feature_importances=model.feature_importances(),
        )

    @staticmethod
    def _evaluate(
        y_true: np.ndarray, y_pred: np.ndarray, n_train: int, n_test: int
    ) -> TrainingMetrics:
        errors = y_pred - y_true
        mae = float(np.mean(np.abs(errors)))
        rmse = float(np.sqrt(np.mean(errors**2)))

        ss_res = float(np.sum(errors**2))
        ss_tot = float(np.sum((y_true - np.mean(y_true)) ** 2))
        r2 = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0

        return TrainingMetrics(
            mae=round(mae, 3),
            rmse=round(rmse, 3),
            r2=round(r2, 3),
            n_train=n_train,
            n_test=n_test,
            trained_at=datetime.now(timezone.utc),
        )
