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
from sqlalchemy import select

from app.core.config import settings
from app.db.session import session_scope
from app.models.squad import SquadState
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


def _resolve_current_gameweek(db) -> int:
    """The latest gameweek with a synced `SquadState`, or 1 if none yet.

    Same fallback DashboardService/scheduler jobs already use - a squad
    snapshot stays valid until the next gameweek's picks are synced, so
    this is "the gameweek to plan for" rather than requiring an exact match.
    """
    latest = (
        db.execute(select(SquadState.gameweek).order_by(SquadState.gameweek.desc()))
        .scalars()
        .first()
    )
    return latest if latest is not None else 1


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
    """Generate predictions, then build the weekly report, for a specific gameweek.

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


@router.post("/run/current", response_model=PipelineResponse)
async def run_weekly_pipeline_current(
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
    """Run the pipeline for whatever gameweek is 'current' right now.

    Resolves the gameweek server-side (latest synced SquadState, or 1 if
    none yet) so external schedulers like cron-job.org never need to be
    manually updated with a hardcoded gameweek number week to week.
    """
    _verify_pipeline_secret(x_pipeline_secret)

    with session_scope() as db:
        gameweek = _resolve_current_gameweek(db)
        result = _pipeline.run(
            db,
            gameweek=gameweek,
            horizons=tuple(horizons),
            evaluate_previous=evaluate_previous,
        )

    return PipelineResponse.from_result(result)