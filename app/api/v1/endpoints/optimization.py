"""
Phase 6: route for requesting a transfer recommendation.

Mirrors `predictions.py`'s convention: one `session_scope()` transaction
per request, with domain errors (`OptimizationError`) translated into a
clean HTTP 422 rather than a raw traceback.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from app.core.exceptions import OptimizationError
from app.db.session import session_scope
from app.optimization.transfer_optimizer import TransferOptimizerService
from app.schemas.optimization import OptimizationResponse

logger = logging.getLogger(__name__)
router = APIRouter()

_optimizer = TransferOptimizerService()


@router.post("/optimize/{gameweek}", response_model=OptimizationResponse)
async def optimize_transfers(
    gameweek: int,
    horizon: int = Query(
        default=1, description="Which stored prediction horizon to optimize against (1, 3, or 5)."
    ),
    max_transfers: int = Query(
        default=2, ge=0, le=3, description="Highest transfer count to evaluate."
    ),
    candidate_pool_size: int = Query(
        default=40, ge=5, le=200, description="Top-N candidates per position fed to the solver."
    ),
) -> OptimizationResponse:
    """Recommend the highest-value transfer move for the upcoming gameweek.

    Requires `DELPHI` predictions for this `gameweek`/`horizon` to already
    exist (`POST /api/v1/predictions/generate/{gameweek}`) and a synced
    squad state - otherwise this returns a 422 explaining what's missing.
    """
    with session_scope() as db:
        try:
            result = _optimizer.optimize(
                db,
                gameweek=gameweek,
                horizon=horizon,
                max_transfers=max_transfers,
                candidate_pool_size=candidate_pool_size,
            )
        except OptimizationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return OptimizationResponse.from_result(result)
