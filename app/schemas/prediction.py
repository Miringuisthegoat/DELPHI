"""API-facing schemas for the Phase 5 prediction engine endpoints."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from app.ml.engine import EvaluationSummary, GenerationSummary, PlayerPrediction
from app.ml.training import TrainingResult


class PlayerPredictionOut(BaseModel):
    """One player's prediction for one horizon."""

    player_id: int
    player_name: str
    position: str
    gameweek: int
    horizon: int
    predicted_points: float
    confidence: float
    model_name: str
    reasoning: str

    @classmethod
    def from_prediction(cls, prediction: PlayerPrediction) -> "PlayerPredictionOut":
        return cls(**prediction.__dict__)


class GenerationResponse(BaseModel):
    """Response for `POST /predictions/generate/{gameweek}`."""

    gameweek: int
    horizons: list[int]
    model_used: str
    players_processed: int
    predictions_created: int
    predictions_updated: int
    predictions: list[PlayerPredictionOut]

    @classmethod
    def from_summary(cls, summary: GenerationSummary) -> "GenerationResponse":
        return cls(
            gameweek=summary.gameweek,
            horizons=list(summary.horizons),
            model_used=summary.model_used,
            players_processed=summary.players_processed,
            predictions_created=summary.predictions_created,
            predictions_updated=summary.predictions_updated,
            predictions=[
                PlayerPredictionOut.from_prediction(p) for p in summary.predictions
            ],
        )


class EvaluationResponse(BaseModel):
    """Response for `POST /predictions/evaluate/{gameweek}`."""

    gameweek: int
    predictions_evaluated: int
    mean_absolute_error: float | None

    @classmethod
    def from_summary(cls, summary: EvaluationSummary) -> "EvaluationResponse":
        return cls(
            gameweek=summary.gameweek,
            predictions_evaluated=summary.predictions_evaluated,
            mean_absolute_error=summary.mean_absolute_error,
        )


class TrainingMetricsOut(BaseModel):
    mae: float
    rmse: float
    r2: float
    n_train: int
    n_test: int
    trained_at: str


class TrainingResponse(BaseModel):
    """Response for `POST /predictions/train`."""

    metrics: TrainingMetricsOut
    model_path: str
    feature_importances: dict[str, float]

    @classmethod
    def from_result(cls, result: TrainingResult) -> "TrainingResponse":
        return cls(
            metrics=TrainingMetricsOut(**result.metrics.to_dict()),
            model_path=result.model_path,
            feature_importances=result.feature_importances,
        )


class PredictionRecordOut(BaseModel):
    """A stored `Prediction` row, as returned by the read-only list endpoint."""

    id: int
    player_id: int
    gameweek: int
    horizon: int
    model_name: str
    model_version: str
    predicted_points: float
    confidence: float
    actual_points: float | None
    prediction_error: float | None

    model_config = ConfigDict(from_attributes=True, protected_namespaces=())
