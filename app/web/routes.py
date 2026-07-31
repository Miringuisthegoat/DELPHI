"""
Phase 8: the dashboard's HTML routes.

Deliberately separate from `app/api/v1/endpoints/*` (which return JSON):
these routes return rendered Jinja2 templates. Kept as a thin adapter -
`DashboardService` owns every query and cross-referencing of Phase 5/6/7
data; this module only resolves the gameweek to show, opens a session,
and renders.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from app.db.session import session_scope
from app.models.squad import SquadState
from app.services.dashboard import DashboardService

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

_dashboard_service = DashboardService()


def _resolve_default_gameweek(db) -> int:
    """Pick a sensible default gameweek: the latest synced `SquadState`, or 1."""
    latest = (
        db.execute(select(SquadState.gameweek).order_by(SquadState.gameweek.desc()))
        .scalars()
        .first()
    )
    return latest if latest is not None else 1


@router.get("/dashboard", response_class=HTMLResponse)
async def read_dashboard(request: Request, gameweek: int | None = None) -> HTMLResponse:
    """Render the main DELPHI dashboard.

    Args:
        gameweek: Gameweek to plan for. Defaults to the most recently
            synced squad gameweek (i.e. "the upcoming decision"), or 1
            if no squad has been synced yet at all.
    """
    with session_scope() as db:
        resolved_gameweek = gameweek if gameweek is not None else _resolve_default_gameweek(db)
        view = _dashboard_service.build_view(db, gameweek=resolved_gameweek)

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={"view": view},
    )
