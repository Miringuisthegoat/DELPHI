"""
DELPHI - Data-driven Expected League Performance using Hybrid Intelligence.

FPL Oracle's prediction engine. "Hybrid" reflects the two predictors
this package provides behind one interface (`DelphiPredictionEngine`):
a transparent rules-based `HeuristicPredictor` for cold-start situations
(preseason, early season) and a trained `RandomForestPointsPredictor`
once enough gameweek history exists, chosen automatically per call.
"""

from __future__ import annotations

from app.ml.engine import (
    DelphiPredictionEngine,
    EvaluationSummary,
    GenerationSummary,
    PlayerPrediction,
)
from app.ml.features import FEATURE_NAMES, FeatureVector, PlayerFeatureBuilder
from app.ml.heuristic import HeuristicPredictor, HeuristicPrediction
from app.ml.model import PointsPredictorModel, RandomForestPointsPredictor, TrainingMetrics
from app.ml.training import ModelTrainingService, TrainingResult

__all__ = [
    "DelphiPredictionEngine",
    "EvaluationSummary",
    "GenerationSummary",
    "PlayerPrediction",
    "FEATURE_NAMES",
    "FeatureVector",
    "PlayerFeatureBuilder",
    "HeuristicPredictor",
    "HeuristicPrediction",
    "PointsPredictorModel",
    "RandomForestPointsPredictor",
    "TrainingMetrics",
    "ModelTrainingService",
    "TrainingResult",
]
