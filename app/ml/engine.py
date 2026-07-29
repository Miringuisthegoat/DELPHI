"""
Phase 5: `DelphiPredictionEngine` - DELPHI's weekly entry point.

This is the class the API/CLI/scheduler actually call. It is responsible
for three things, matching the project prompt's "predict future points"
and "learning system" requirements:

1. **Choosing a predictor.** If a trained Random Forest artifact exists
   on disk *and* the database now holds enough history to trust it
   (`settings.ml_min_samples_for_training`), use it. Otherwise fall back
   to `HeuristicPredictor` - this is what makes the engine usable from
   the very first day of a season, not just once months of data exist.
2. **Persisting predictions.** Every prediction, for every horizon,
   is upserted into the `predictions` table (same upsert-by-natural-key
   pattern as `DataIngestionService` in Phase 4), so nothing is ever lost
   and re-running a gameweek's predictions just refines them.
3. **Closing the loop.** Once a gameweek has actually been played,
   `evaluate_gameweek` fills in `actual_points`/`prediction_error` on the
   horizon-1 predictions made for it, which is the raw material the
   project's "learning system" (comparing predicted vs actual over time)
   depends on.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.player import Player
from app.models.player_stats import PlayerGameweekStats
from app.models.prediction import Prediction

from app.ml.features import FeatureVector, PlayerFeatureBuilder
from app.ml.heuristic import HeuristicPredictor
from app.ml.model import RandomForestPointsPredictor


@dataclass
class PlayerPrediction:
    """One player's prediction for one horizon, ready for the API/report layer."""

    player_id: int
    player_name: str
    position: str
    gameweek: int
    horizon: int
    predicted_points: float
    confidence: float
    model_name: str
    reasoning: str


@dataclass
class GenerationSummary:
    """Outcome of generating predictions for a gameweek across all horizons."""

    gameweek: int
    horizons: tuple[int, ...]
    model_used: str
    players_processed: int
    predictions_created: int
    predictions_updated: int
    predictions: list[PlayerPrediction] = field(default_factory=list)


@dataclass
class EvaluationSummary:
    """Outcome of comparing horizon-1 predictions to what actually happened."""

    gameweek: int
    predictions_evaluated: int
    mean_absolute_error: float | None


class DelphiPredictionEngine:
    """Generates, persists, and evaluates DELPHI's player point predictions."""

    def __init__(self, feature_builder: PlayerFeatureBuilder | None = None) -> None:
        self._feature_builder = feature_builder or PlayerFeatureBuilder()
        self._heuristic = HeuristicPredictor()
        self._rf_model: RandomForestPointsPredictor | None = None

    # --- Model selection -----------------------------------------------------

    def _load_rf_model_if_usable(self, db: Session) -> RandomForestPointsPredictor | None:
        """Load the persisted Random Forest model, if one exists and is trustworthy.

        "Trustworthy" here means the database currently holds at least
        `settings.ml_min_samples_for_training` historical rows - a model
        trained on a previous, larger dataset shouldn't quietly keep
        being used if e.g. the database was reset for a new season.
        """
        if self._rf_model is not None:
            return self._rf_model

        artifact_path = RandomForestPointsPredictor.latest_artifact_path(
            settings.ml_model_dir
        )
        if artifact_path is None:
            return None

        history_count = db.execute(
            select(PlayerGameweekStats.id).limit(settings.ml_min_samples_for_training)
        ).all()
        if len(history_count) < settings.ml_min_samples_for_training:
            logger.info(
                "A trained DELPHI model exists but only {} history rows are "
                "in the database (need {}); using the heuristic predictor "
                "instead.",
                len(history_count),
                settings.ml_min_samples_for_training,
            )
            return None

        model = RandomForestPointsPredictor(version=settings.ml_model_version)
        model.load(artifact_path)
        self._rf_model = model
        return model

    # --- Generation ------------------------------------------------------------

    def generate_for_gameweek(
        self,
        db: Session,
        gameweek: int,
        horizons: tuple[int, ...] | None = None,
        active_only: bool = True,
    ) -> GenerationSummary:
        """Generate and persist predictions for every eligible player.

        Args:
            db: Active SQLAlchemy session (caller commits, per the Phase 4
                convention - see `session_scope`).
            gameweek: The upcoming gameweek to predict for.
            horizons: Which horizons to predict (in gameweeks). Defaults
                to `settings.ml_default_horizons` (1, 3, 5).
            active_only: Skip players who have left the player pool
                (`Player.is_active is False`). Unavailable-but-active
                players (injured/suspended) are still predicted - their
                low `expected_minutes_probability` naturally suppresses
                their projection rather than hiding them from the report.
        """
        horizons = horizons or settings.ml_default_horizons

        rf_model = self._load_rf_model_if_usable(db)
        model_label = "rf" if rf_model is not None else "heuristic"

        query = select(Player)
        if active_only:
            query = query.where(Player.is_active.is_(True))
        players = db.execute(query).scalars().all()

        created = 0
        updated = 0
        results: list[PlayerPrediction] = []

        for player in players:
            for horizon in horizons:
                predicted_points, confidence, reasoning = self._predict_horizon(
                    db, player, gameweek, horizon, rf_model
                )
                model_name = f"{settings.ml_model_name}_{model_label}"

                created_flag = self._upsert_prediction(
                    db=db,
                    player_id=player.id,
                    gameweek=gameweek,
                    horizon=horizon,
                    predicted_points=predicted_points,
                    confidence=confidence,
                    model_name=model_name,
                )
                if created_flag:
                    created += 1
                else:
                    updated += 1

                results.append(
                    PlayerPrediction(
                        player_id=player.id,
                        player_name=player.web_name,
                        position=player.position.value,
                        gameweek=gameweek,
                        horizon=horizon,
                        predicted_points=predicted_points,
                        confidence=confidence,
                        model_name=model_name,
                        reasoning=reasoning,
                    )
                )

        db.flush()
        logger.info(
            "DELPHI generated predictions for gw {} ({} players x {} horizons) "
            "using the {} model: {} created, {} updated",
            gameweek,
            len(players),
            len(horizons),
            model_label,
            created,
            updated,
        )

        return GenerationSummary(
            gameweek=gameweek,
            horizons=tuple(horizons),
            model_used=model_label,
            players_processed=len(players),
            predictions_created=created,
            predictions_updated=updated,
            predictions=results,
        )

    def _predict_horizon(
        self,
        db: Session,
        player: Player,
        gameweek: int,
        horizon: int,
        rf_model: RandomForestPointsPredictor | None,
    ) -> tuple[float, float, str]:
        """Sum single-gameweek predictions across `horizon` gameweeks.

        Fixture-dependent features (difficulty, home/away, opponent
        strength) are recomputed for each gameweek in the window, since a
        player's run of fixtures can swing from easy to hard within a
        3-5 gameweek horizon; player-level rolling history is naturally
        held fixed across the window since it's built strictly from
        gameweeks before the *first* one in the window.
        """
        total_points = 0.0
        confidences: list[float] = []
        reasoning_parts: list[str] = []
        gameweeks_in_window = range(gameweek, gameweek + horizon)

        for gw in gameweeks_in_window:
            vector = self._feature_builder.build(db, player, gw)

            if rf_model is not None:
                points = float(rf_model.predict(_as_matrix(vector))[0])
                confidence = 0.75
                reasoning_parts.append(
                    f"gw{gw}: {points:.1f} pts (model-predicted from recent "
                    "form and fixture data)"
                )
            else:
                estimate = self._heuristic.predict(vector)
                points = estimate.predicted_points
                confidence = estimate.confidence
                reasoning_parts.append(f"gw{gw}: {estimate.reasoning}")

            total_points += points
            confidences.append(confidence)

        # Confidence decays with horizon length - predicting 5 gameweeks
        # out is inherently less certain than predicting the next one,
        # regardless of which underlying model is used.
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0
        horizon_decay = 1.0 - (0.06 * (horizon - 1))
        confidence = round(max(avg_confidence * max(horizon_decay, 0.5), 0.05), 2)

        reasoning = " ".join(reasoning_parts)
        return round(total_points, 2), confidence, reasoning

    def _upsert_prediction(
        self,
        db: Session,
        player_id: int,
        gameweek: int,
        horizon: int,
        predicted_points: float,
        confidence: float,
        model_name: str,
    ) -> bool:
        """Insert or update the `(player_id, gameweek, horizon)` prediction.

        Returns True if a new row was created, False if an existing one
        was updated in place.
        """
        existing = (
            db.query(Prediction)
            .filter_by(player_id=player_id, gameweek=gameweek, horizon=horizon)
            .one_or_none()
        )
        if existing is None:
            db.add(
                Prediction(
                    player_id=player_id,
                    gameweek=gameweek,
                    horizon=horizon,
                    model_name=model_name,
                    model_version=settings.ml_model_version,
                    predicted_points=predicted_points,
                    confidence=confidence,
                )
            )
            return True

        existing.predicted_points = predicted_points
        existing.confidence = confidence
        existing.model_name = model_name
        existing.model_version = settings.ml_model_version
        # A re-predicted gameweek supersedes any previously recorded
        # outcome until the new prediction is itself evaluated again.
        existing.actual_points = None
        existing.prediction_error = None
        return False

    # --- Evaluation / learning loop ---------------------------------------------

    def evaluate_gameweek(self, db: Session, gameweek: int) -> EvaluationSummary:
        """Backfill actual outcomes for horizon-1 predictions of `gameweek`.

        Intended to run once a gameweek has finished and its
        `PlayerGameweekStats` have been synced (Phase 4). This is the
        "learning system": every `Prediction` row keeps a permanent record
        of `predicted_points` vs `actual_points`, so prediction accuracy
        can be tracked and the training set for the *next* `train()` call
        keeps growing.
        """
        predictions = (
            db.query(Prediction)
            .filter_by(gameweek=gameweek, horizon=1)
            .all()
        )

        errors: list[float] = []
        evaluated = 0

        for prediction in predictions:
            stats = (
                db.query(PlayerGameweekStats)
                .filter_by(player_id=prediction.player_id, gameweek=gameweek)
                .one_or_none()
            )
            if stats is None:
                continue

            prediction.record_actual(float(stats.total_points))
            errors.append(abs(prediction.prediction_error))
            evaluated += 1

        db.flush()
        mae = round(sum(errors) / len(errors), 3) if errors else None

        logger.info(
            "DELPHI evaluation for gw {}: {} predictions evaluated, MAE={}",
            gameweek,
            evaluated,
            mae if mae is not None else "n/a (no matching stats synced yet)",
        )

        return EvaluationSummary(
            gameweek=gameweek,
            predictions_evaluated=evaluated,
            mean_absolute_error=mae,
        )


def _as_matrix(vector: FeatureVector) -> np.ndarray:
    """Wrap a single `FeatureVector` as the 2D array shape sklearn expects."""
    return np.array([vector.to_row()], dtype=float)
