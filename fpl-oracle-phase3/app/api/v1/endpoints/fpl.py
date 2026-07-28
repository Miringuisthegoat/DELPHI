"""
Diagnostic/manual-trigger endpoints for the FPL API integration layer.

These routes exist so Phase 3 can be verified end-to-end (hit an
endpoint, see real typed data come back) before Phase 4 wires the
same client into a scheduled ingestion job that persists to the
database. They are intentionally read-only and side-effect-free.

Wire this into your existing router aggregator, e.g. in
``app/api/v1/api.py``:

    from app.api.v1.endpoints import fpl

    api_router.include_router(fpl.router, prefix="/fpl", tags=["fpl-integration"])
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from app.schemas.fpl_bootstrap import FPLBootstrapStatic
from app.schemas.fpl_element_summary import FPLElementSummary
from app.schemas.fpl_fixtures import FPLFixture
from app.schemas.fpl_live import FPLEventLive
from app.services.fpl_api import FPLAPIClient, FPLAPIError, FPLNotFoundError

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/bootstrap-static", response_model=FPLBootstrapStatic)
async def read_bootstrap_static() -> FPLBootstrapStatic:
    """Fetch the full bootstrap-static payload (all players, teams,
    gameweeks). Useful for confirming the client + schemas work
    against the live API before wiring up scheduled ingestion."""
    async with FPLAPIClient() as client:
        try:
            return await client.get_bootstrap_static()
        except FPLAPIError as exc:
            logger.exception("bootstrap-static fetch failed")
            raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/fixtures", response_model=list[FPLFixture])
async def read_fixtures(
    event: int | None = Query(default=None, description="Gameweek to filter to, if any"),
) -> list[FPLFixture]:
    """Fetch fixtures, optionally filtered to a single gameweek."""
    async with FPLAPIClient() as client:
        try:
            return await client.get_fixtures(event=event)
        except FPLAPIError as exc:
            logger.exception("fixtures fetch failed")
            raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/players/{player_id}/summary", response_model=FPLElementSummary)
async def read_player_summary(player_id: int) -> FPLElementSummary:
    """Fetch a single player's gameweek history and upcoming fixtures."""
    async with FPLAPIClient() as client:
        try:
            return await client.get_element_summary(player_id)
        except FPLNotFoundError as exc:
            raise HTTPException(
                status_code=404, detail=f"No FPL player with id {player_id}"
            ) from exc
        except FPLAPIError as exc:
            logger.exception("element-summary fetch failed for player %s", player_id)
            raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/gameweeks/{event_id}/live", response_model=FPLEventLive)
async def read_gameweek_live(event_id: int) -> FPLEventLive:
    """Fetch live/completed stats for every player in a given gameweek."""
    async with FPLAPIClient() as client:
        try:
            return await client.get_event_live(event_id)
        except FPLAPIError as exc:
            logger.exception("event-live fetch failed for event %s", event_id)
            raise HTTPException(status_code=502, detail=str(exc)) from exc
