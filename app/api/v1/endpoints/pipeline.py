"""
Phase 10: one route that runs the full local weekly workflow.

Mirrors the project's usual convention (`predictions.py`, `reports.py`):
one `session_scope()` transaction per request. Unlike those routes, this
one deliberately chains several services together (see
`WeeklyPipelineService` for why sync/squad-pull stays out of this call).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Header, HTTPException, Query

from app.core.config import settings
from app.db.session import session_scope
from app.schemas.pipeline import PipelineResponse
from app.services.pipeline import WeeklyPipelineService

logger = logging.getLogger(__name__)
router = APIRouter()

_pipeline = WeeklyPipelineService()


def _verify_pipeline_secret(x_pipeline_secret: str | None) -> None:
    """Reject the request unless it carries the configured shared secret.

    Skipped entirely if settings.pipeline_secret is unset (local dev) -
    but this endpoint is public once deployed, so the secret MUST be set
    in production or anyone can trigger (harmless but wasteful) pipeline runs.
    """
    if settings.pipeline_secret is None:
        return
    if x_pipeline_secret != settings.pipeline_secret:
        raise HTTPException(status_code=401, detail="Invalid or missing pipeline secret.")


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
    x_pipeline_secret: str | None = Header(default=None),
) -> PipelineResponse:
    """Generate predictions, then build the weekly report, in one call.

    Requires the X-Pipeline-Secret header to match settings.pipeline_secret
    once that setting is configured (see app/core/config.py).
    """
    _verify_pipeline_secret(x_pipeline_secret)

    with session_scope() as db:
        result = _pipeline.run(
            db,
            gameweek=gameweek,
            horizons=tuple(horizons),
            evaluate_previous=evaluate_previous,
        )

    return PipelineResponse.from_result(result)