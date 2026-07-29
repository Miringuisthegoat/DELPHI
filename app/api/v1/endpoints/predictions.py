"""
Phase 5: routes for training DELPHI, generating predictions, and
evaluating them once a gameweek has been played.

Mirrors `sync.py`'s convention: each route owns exactly one
`session_scope()` transaction and translates domain errors
(`PredictionError`) into clean HTTP responses.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.core.config import settings
from app.core.exceptions import PredictionError
from app.db.session import session_scope
from app.ml.engine import DelphiPredictionEngine
from app.ml.training import ModelTrainingService
from app.models.prediction import Prediction
from app.schemas.prediction import (
    EvaluationResponse,
    GenerationResponse,
    PredictionRecordOut,
    TrainingResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter()

_engine = DelphiPredictionEngine()
_trainer = ModelTrainingService()


@router.post("/train", response_model=TrainingResponse)
async def train_model() -> TrainingResponse:
    """Train (or retrain) the DELPHI Random Forest model from DB history.

    Requires at least `settings.ml_min_samples_for_training` historical
    (player, gameweek) rows to already be synced (Phase 4). Early in a
    season this will return a 422 explaining that there isn't enough
    data yet - that's expected, not a bug; `generate` keeps working via
    the heuristic predictor in the meantime.
    """
    with session_scope() as db:
        try:
            result = _trainer.train(db)
        except PredictionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return TrainingResponse.from_result(result)


@router.post("/generate/{gameweek}", response_model=GenerationResponse)
async def generate_predictions(
    gameweek: int,
    horizons: list[int] = Query(
        default=list(settings.ml_default_horizons),
        description="Gameweek horizons to predict, e.g. ?horizons=1&horizons=3&horizons=5",
    ),
) -> GenerationResponse:
    """Generate and persist point predictions for every active player.

    Uses the trained Random Forest model if one exists and enough
    history has accumulated to trust it, otherwise falls back to the
    transparent heuristic predictor (see `HeuristicPredictor`).
    """
    with session_scope() as db:
        summary = _engine.generate_for_gameweek(
            db, gameweek=gameweek, horizons=tuple(horizons)
        )

    return GenerationResponse.from_summary(summary)


@router.post("/evaluate/{gameweek}", response_model=EvaluationResponse)
async def evaluate_predictions(gameweek: int) -> EvaluationResponse:
    """Backfill actual outcomes for a gameweek's horizon-1 predictions.

    Run this after `POST /api/v1/sync/gameweeks/{gameweek}/live` (or a
    full history sync) has populated that gameweek's actual results -
    this is what powers DELPHI's "learning system" of predicted vs.
    actual points over time.
    """
    with session_scope() as db:
        summary = _engine.evaluate_gameweek(db, gameweek=gameweek)

    return EvaluationResponse.from_summary(summary)


@router.get("/{gameweek}", response_model=list[PredictionRecordOut])
async def list_predictions(
    gameweek: int,
    horizon: int | None = Query(default=None, description="Filter to one horizon."),
    player_id: int | None = Query(default=None, description="Filter to one player."),
) -> list[PredictionRecordOut]:
    """List stored predictions for a gameweek, optionally filtered."""
    with session_scope() as db:
        query = select(Prediction).where(Prediction.gameweek == gameweek)
        if horizon is not None:
            query = query.where(Prediction.horizon == horizon)
        if player_id is not None:
            query = query.where(Prediction.player_id == player_id)

        rows = db.execute(query).scalars().all()
        return [PredictionRecordOut.model_validate(row) for row in rows]
