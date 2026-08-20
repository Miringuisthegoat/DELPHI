"""
Phase 8 (cont.): the dashboard's HTML routes.

Deliberately separate from `app/api/v1/endpoints/*` (which return JSON):
these routes return rendered Jinja2 templates. Kept as a thin adapter -
`DashboardService`/`WeeklyReportService` own every query and cross-
referencing of Phase 5/6/7/9 data; this module only resolves the
gameweek to show, opens a session, and renders.

Sidebar wiring
--------------
`base.html`'s 7 sidebar icons map to the 7 routes below (dashboard is
also the logo link). Every page except `/menu` and `/settings` needs a
gameweek-scoped view, so they all follow the same "resolve default
gameweek -> build view -> render" shape `read_dashboard` already used;
`/squad`, `/transfers`, and `/profile` reuse the exact same
`DashboardView` `read_dashboard` builds (no new business logic, no new
queries) and just render a different template against it. `/reports`
reuses `WeeklyReportService` (Phase 9), which itself is a formatter over
`DashboardService`. `/menu` and `/settings` are static/config pages with
no gameweek concept.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from app.core.config import settings
from app.db.session import session_scope
from app.models.squad import SquadState
from app.services.dashboard import DashboardService
from app.services.fpl_api import FPLAPIClient, FPLAPIError
from app.services.reporting import ConsoleDeliveryChannel, WeeklyReportService

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

_dashboard_service = DashboardService()
_report_service = WeeklyReportService()


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


@router.get("/squad", response_class=HTMLResponse)
async def read_squad_page(request: Request, gameweek: int | None = None) -> HTMLResponse:
    """Full 15-man squad table + injury alerts, reusing the same `DashboardView`."""
    with session_scope() as db:
        resolved_gameweek = gameweek if gameweek is not None else _resolve_default_gameweek(db)
        view = _dashboard_service.build_view(db, gameweek=resolved_gameweek)

    return templates.TemplateResponse(
        request=request,
        name="squad.html",
        context={"view": view},
    )


@router.get("/transfers", response_class=HTMLResponse)
async def read_transfers_page(request: Request, gameweek: int | None = None) -> HTMLResponse:
    """DELPHI's transfer recommendation for the gameweek, reusing `DashboardView`."""
    with session_scope() as db:
        resolved_gameweek = gameweek if gameweek is not None else _resolve_default_gameweek(db)
        view = _dashboard_service.build_view(db, gameweek=resolved_gameweek)

    return templates.TemplateResponse(
        request=request,
        name="transfers.html",
        context={"view": view},
    )


async def _fetch_team_identity() -> dict[str, str | None]:
    """Look up the manager's team/player name from the FPL `entry` endpoint.

    Falls back to `None`s (the template shows the raw team id instead)
    if `FPL_TEAM_ID` isn't set or the live API call fails - a name lookup
    failing shouldn't take down the whole profile page.
    """
    if settings.fpl_team_id is None:
        return {"team_name": None, "manager_name": None}

    async with FPLAPIClient() as client:
        try:
            entry = await client.get_entry(settings.fpl_team_id)
        except FPLAPIError as exc:
            logger.warning("Could not fetch FPL entry %s: %s", settings.fpl_team_id, exc)
            return {"team_name": None, "manager_name": None}

    first = entry.get("player_first_name", "")
    last = entry.get("player_last_name", "")
    manager_name = f"{first} {last}".strip() or None

    return {"team_name": entry.get("name"), "manager_name": manager_name}


@router.get("/profile", response_class=HTMLResponse)
async def read_profile_page(request: Request, gameweek: int | None = None) -> HTMLResponse:
    """Manager identity + season snapshot, reusing `DashboardView`."""
    identity = await _fetch_team_identity()

    with session_scope() as db:
        resolved_gameweek = gameweek if gameweek is not None else _resolve_default_gameweek(db)
        view = _dashboard_service.build_view(db, gameweek=resolved_gameweek)

    return templates.TemplateResponse(
        request=request,
        name="profile.html",
        context={
            "view": view,
            "fpl_team_id": settings.fpl_team_id,
            "team_name": identity["team_name"],
            "manager_name": identity["manager_name"],
        },
    )


@router.get("/reports", response_class=HTMLResponse)
async def read_reports_page(request: Request, gameweek: int | None = None) -> HTMLResponse:
    """Weekly prose report (Phase 9's `WeeklyReportService`) rendered as HTML."""
    with session_scope() as db:
        resolved_gameweek = gameweek if gameweek is not None else _resolve_default_gameweek(db)
        report = _report_service.build_report(db, gameweek=resolved_gameweek)

    return templates.TemplateResponse(
        request=request,
        name="reports.html",
        context={"report": report},
    )


@router.post("/reports/{gameweek}/send")
async def send_reports_page(gameweek: int) -> RedirectResponse:
    """Deliver the report via the console channel, then bounce back to /reports.

    Mirrors `POST /api/v1/reports/{gameweek}/send` (Phase 9's JSON route)
    but is a plain form target for the HTML page, so no JS is required.
    """
    with session_scope() as db:
        report = _report_service.build_report(db, gameweek=gameweek)

    try:
        ConsoleDeliveryChannel().send(report)
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc

    return RedirectResponse(url=f"/reports?gameweek={gameweek}", status_code=303)

@router.post("/dashboard/sync-squad/{gameweek}")
async def sync_squad_from_dashboard(gameweek: int) -> RedirectResponse:
    """Sync 'My Squad' for `gameweek`, then bounce back to the dashboard.

    Mirrors `POST /api/v1/squad/sync/{gameweek}` (the JSON API route) but
    is a plain form target for the dashboard's Sync button, so no JS is
    required. Errors are caught and surfaced via a query param rather
    than a raw 4xx/5xx page, since this is a user-facing button, not an
    API client.
    """
    if settings.fpl_team_id is None:
        return RedirectResponse(
            url=f"/dashboard?gameweek={gameweek}&sync_error="
            "FPL_TEAM_ID+is+not+configured",
            status_code=303,
        )

    async with FPLAPIClient() as client:
        try:
            picks_payload = await client.get_entry_event_picks(
                entry_id=settings.fpl_team_id, event_id=gameweek
            )
        except FPLAPIError as exc:
            logger.warning(
                "Dashboard squad sync failed for gw %s: %s", gameweek, exc
            )
            return RedirectResponse(
                url=f"/dashboard?gameweek={gameweek}&sync_error={exc}",
                status_code=303,
            )

    with session_scope() as db:
        from app.services.squad import SquadSyncService

        SquadSyncService().sync_from_fpl_payloads(
            db, gameweek=gameweek, picks_payload=picks_payload
        )

    return RedirectResponse(url=f"/dashboard?gameweek={gameweek}", status_code=303)


@router.get("/menu", response_class=HTMLResponse)
async def read_menu_page(request: Request) -> HTMLResponse:
    """Static navigation hub - every HTML page and JSON API family in one place."""
    return templates.TemplateResponse(
        request=request,
        name="menu.html",
        context={},
    )


@router.get("/settings", response_class=HTMLResponse)
async def read_settings_page(request: Request) -> HTMLResponse:
    """Read-only view of current `Settings` - env, scheduler, ML config."""
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={"settings": settings},
    )
