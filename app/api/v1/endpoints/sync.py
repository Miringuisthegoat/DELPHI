"""
Phase 4: routes that trigger persisting FPL API data into the database.

Unlike `fpl.py` (Phase 3's read-only, side-effect-free diagnostic routes),
every route here writes to the database. Each route owns exactly one
`session_scope()` transaction: fetch from the live API, sync, and commit
(or roll back) as a single unit.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db.session import session_scope
from app.services.fpl_api import FPLAPIClient, FPLAPIError, FPLNotFoundError
from app.services.ingestion import DataIngestionService, IngestionResult

logger = logging.getLogger(__name__)
router = APIRouter()

_ingestion = DataIngestionService()


class IngestionResultOut(BaseModel):
    """API-friendly view of `IngestionResult`."""

    created: int
    updated: int
    failed: int
    errors: list[str]

    @classmethod
    def from_result(cls, result: IngestionResult) -> "IngestionResultOut":
        return cls(
            created=result.created,
            updated=result.updated,
            failed=result.failed,
            errors=result.errors,
        )


class FullSyncResponse(BaseModel):
    """API-friendly view of `FullSyncSummary`."""

    teams: IngestionResultOut
    players: IngestionResultOut
    fixtures: IngestionResultOut
    duration_seconds: float


@router.post("/full", response_model=FullSyncResponse)
async def sync_full() -> FullSyncResponse:
    """Download bootstrap-static + fixtures and persist everything.

    This is the routine "weekly workflow, step 1" sync: teams, players
    (with current price/form/ownership/status), and the full fixture
    list with FDR ratings for every remaining gameweek.
    """
    async with FPLAPIClient() as client:
        try:
            bootstrap = await client.get_bootstrap_static()
            fixtures = await client.get_fixtures()
        except FPLAPIError as exc:
            logger.exception("Full sync: FPL API fetch failed")
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    with session_scope() as db:
        summary = _ingestion.sync_full_bootstrap(db, bootstrap, fixtures)

    return FullSyncResponse(
        teams=IngestionResultOut.from_result(summary.teams),
        players=IngestionResultOut.from_result(summary.players),
        fixtures=IngestionResultOut.from_result(summary.fixtures),
        duration_seconds=summary.duration_seconds,
    )


@router.post("/players/{player_id}/history", response_model=IngestionResultOut)
async def sync_player_history(player_id: int) -> IngestionResultOut:
    """Backfill one player's full season history from `element-summary`.

    Call `sync_full` first so the player already has a `players` row to
    attach history to.
    """
    async with FPLAPIClient() as client:
        try:
            summary = await client.get_element_summary(player_id)
        except FPLNotFoundError as exc:
            raise HTTPException(
                status_code=404, detail=f"No FPL player with id {player_id}"
            ) from exc
        except FPLAPIError as exc:
            logger.exception("History sync failed for player %s", player_id)
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    with session_scope() as db:
        result = _ingestion.sync_player_history(db, player_id=player_id, summary=summary)

    return IngestionResultOut.from_result(result)


@router.post("/gameweeks/{gameweek}/live", response_model=IngestionResultOut)
async def sync_gameweek_live(gameweek: int) -> IngestionResultOut:
    """Sync near-real-time stats for one gameweek from `event/{id}/live`.

    Cheaper than backfilling every player's `element-summary` and
    suitable for polling during/shortly after live gameweeks; bonus
    points may still be provisional until fixtures are fully finished.
    """
    async with FPLAPIClient() as client:
        try:
            live = await client.get_event_live(gameweek)
        except FPLAPIError as exc:
            logger.exception("Live sync failed for gameweek %s", gameweek)
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    with session_scope() as db:
        result = _ingestion.sync_gameweek_live(db, gameweek=gameweek, live=live)

    return IngestionResultOut.from_result(result)
