"""
Phase 5 (+ Phase 12/13): `ModelTrainingService` - turns accumulated
gameweek history into a trained DELPHI model.

Phase 12: `build_training_data()` can blend in `HistoricalPlayerGameweekStats`
rows (prior seasons, pulled from vaastav/Fantasy-Premier-League - see
`app.services.historical`) alongside the current season's
`PlayerGameweekStats`. See PHASE_12_README.md for the full rationale.

Phase 13: `FEATURE_NAMES` grew four new columns (`cbi_avg_5`,
`tackles_avg_5`, `recoveries_avg_5`, `defensive_contribution_avg_5` - see
`app.ml.features`) to capture the 2025-26 "defensive contribution"
scoring rule. This is a breaking change to the model's input shape:
`RandomForestPointsPredictor.load()` already guards against silently
using a stale artifact (`stored_features != FEATURE_NAMES` raises
`ValueError` - see `app/ml/model.py`), so any previously-trained model
on disk simply won't load anymore and `train()` must be re-run. This is
intentional - an old model has no learned relationship for a feature it
was never shown.
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

try:
    from app.models.player_stats_historical import HistoricalPlayerGameweekStats

    _HISTORICAL_MODEL_AVAILABLE = True
except ImportError:  # pragma: no cover - Phase 12 not yet merged in
    _HISTORICAL_MODEL_AVAILABLE = False


@dataclass
class TrainingResult:
    """Everything the API/CLI needs to report back after a training run."""

    metrics: TrainingMetrics
    model_path: str
    feature_importances: dict[str, float]
    historical_rows_used: int = 0
    """Count of pretraining rows drawn from prior seasons (Phase 12).
    0 if historical data wasn't requested/available for this run."""


class ModelTrainingService:
    """Builds the training set from the database and fits `RandomForestPointsPredictor`."""

    def __init__(self, feature_builder: PlayerFeatureBuilder | None = None) -> None:
        self._feature_builder = feature_builder or PlayerFeatureBuilder()

    def build_training_data(
        self, db: Session, include_historical: bool = True
    ) -> tuple[np.ndarray, np.ndarray, int]:
        """Build (X, y, historical_row_count) from every historical result.

        For every `PlayerGameweekStats` row (an actual, already-played
        current-season gameweek), the feature vector is reconstructed
        exactly as it would have looked *before* that gameweek was played
        (see `PlayerFeatureBuilder`'s no-lookahead rule), and the row's
        actual `total_points` becomes the training label. Rows with zero
        prior history (a player's very first recorded gameweek) are
        skipped - with nothing to compute rolling features from, they'd
        all be default/neutral values and would just teach the model noise.

        If `include_historical` is True and Phase 12's historical
        ingestion has been run, matched `HistoricalPlayerGameweekStats`
        rows from prior seasons are unioned in as additional training
        examples (see module docstring for why the no-lookahead
        restriction doesn't apply to them). Rows from seasons before
        2025-26 correctly contribute 0 for the Phase 13
        defensive-contribution features (the rule didn't exist yet), not
        because of any Phase 12 data-quality gap.
        """
        features, targets = self._build_current_season_rows(db)
        historical_count = 0

        if include_historical and _HISTORICAL_MODEL_AVAILABLE:
            hist_features, hist_targets, historical_count = (
                self._build_historical_rows(db)
            )
            features.extend(hist_features)
            targets.extend(hist_targets)

        if not features:
            return (
                np.empty((0, len(FEATURE_NAMES))),
                np.empty((0,)),
                historical_count,
            )

        return (
            np.array(features, dtype=float),
            np.array(targets, dtype=float),
            historical_count,
        )

    def _build_current_season_rows(
        self, db: Session
    ) -> tuple[list[list[float]], list[float]]:
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

        return features, targets

    def _build_historical_rows(
        self, db: Session
    ) -> tuple[list[list[float]], list[float], int]:
        """Phase 12: build training rows from matched prior-season data.

        A historical row's feature vector is built using the CURRENT
        player's up-to-date feature builder (position, price, etc. as
        known today) combined with that historical row's own performance
        stats as the label - this is a deliberate simplification (the
        player's price/team/fixtures back then differed from today), but
        it's a reasonable trade-off for pretraining, since the goal is
        teaching the model general price/form/fixture -> points patterns,
        not perfectly reconstructing a bygone gameweek's exact context.
        Revisit if evaluation shows this hurts more than helps.
        """
        if not _HISTORICAL_MODEL_AVAILABLE:
            return [], [], 0

        rows = (
            db.execute(
                select(HistoricalPlayerGameweekStats).where(
                    HistoricalPlayerGameweekStats.matched_player_id.is_not(None)
                )
            )
            .scalars()
            .all()
        )

        features: list[list[float]] = []
        targets: list[float] = []
        player_cache: dict[int, Player | None] = {}
        skipped_unmatched = 0

        for row in rows:
            player = player_cache.get(row.matched_player_id)
            if player is None and row.matched_player_id not in player_cache:
                player = db.get(Player, row.matched_player_id)
                player_cache[row.matched_player_id] = player
            if player is None:
                skipped_unmatched += 1
                continue

            # No no-lookahead restriction needed: this is a fully-played
            # past season, so any target_gameweek within it is safe -
            # arbitrarily use a far-future "target" so PlayerFeatureBuilder's
            # `gameweek < target` filter (which queries PlayerGameweekStats,
            # not historical rows) simply returns whatever current-season
            # context exists, if any.
            vector = self._feature_builder.build(db, player, target_gameweek=10_000)
            features.append(vector.to_row())
            targets.append(float(row.total_points))

        if skipped_unmatched:
            logger.debug(
                "Skipped {} historical rows whose matched_player_id no "
                "longer resolves to a current Player row.",
                skipped_unmatched,
            )

        return features, targets, len(features)

    def train(self, db: Session, include_historical: bool = True) -> TrainingResult:
        """Build the training set, fit the model, evaluate, and persist it.

        Args:
            db: Active SQLAlchemy session.
            include_historical: Whether to blend in Phase 12's matched
                prior-season rows alongside current-season data. Default
                True - safe even if Phase 12 hasn't been run yet, since
                `build_training_data` no-ops the historical portion when
                the table/model isn't present or is empty.

        Raises:
            PredictionError: if fewer than
                `settings.ml_min_samples_for_training` labelled rows are
                available in total (current-season + historical combined).
        """
        X, y, historical_count = self.build_training_data(
            db, include_historical=include_historical
        )

        if X.shape[0] < settings.ml_min_samples_for_training:
            raise PredictionError(
                f"Only {X.shape[0]} labelled training rows are available "
                f"(need at least {settings.ml_min_samples_for_training}), "
                f"of which {historical_count} came from historical seasons. "
                "This is expected in preseason or the first few gameweeks "
                "of a season if no historical data has been ingested yet - "
                "run `python -m scripts.sync_historical_data` (Phase 12) "
                "to pretrain on prior seasons, or wait for more in-season "
                "gameweeks to accumulate."
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
            "(train={}, test={}, historical_rows={})",
            metrics.mae,
            metrics.rmse,
            metrics.r2,
            metrics.n_train,
            metrics.n_test,
            historical_count,
        )

        return TrainingResult(
            metrics=metrics,
            model_path=str(artifact_path),
            feature_importances=model.feature_importances(),
            historical_rows_used=historical_count,
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
