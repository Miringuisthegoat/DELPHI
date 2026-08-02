"""
Phase 10: one route that runs the full local weekly workflow.

Mirrors the project's usual convention (`predictions.py`, `reports.py`):
one `session_scope()` transaction per request. Unlike those routes, this
one deliberately chains several services together (see
`WeeklyPipelineService` for why sync/squad-pull stays out of this call).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query

from app.core.config import settings
from app.db.session import session_scope
from app.schemas.pipeline import PipelineResponse
from app.services.pipeline import WeeklyPipelineService

logger = logging.getLogger(__name__)
router = APIRouter()

_pipeline = WeeklyPipelineService()


@router.post("/run/{gameweek}", response_model=PipelineResponse)
async def run_weekly_pipeline(
    gameweek: int,
    horizons: list[int] = Query(
        default=list(settings.ml_default_horizons),
        description="Horizons to (re)generate predictions for.",
    ),
    evaluate_previous: bool = Query(
        default=True,
        description="Also backfill actual outcomes for gameweek-1 before reporting.",
    ),
) -> PipelineResponse:
    """Generate predictions, then build the weekly report, in one call.

    Requires a squad to already be synced for this gameweek
    (`POST /api/v1/squad/sync/{gameweek}`) to get transfer suggestions
    and projected points - if not, the report explains what's missing
    rather than failing the whole request (see `WeeklyReportService`).
    """
    with session_scope() as db:
        result = _pipeline.run(
            db,
            gameweek=gameweek,
            horizons=tuple(horizons),
            evaluate_previous=evaluate_previous,
        )

    return PipelineResponse.from_result(result)
