"""
Phase 7: routes for syncing and reading "My Squad".

Mirrors `sync.py`'s convention: fetch from the live FPL API inside an
`async with FPLAPIClient()` block, then persist inside exactly one
`session_scope()` transaction.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.config import settings
from app.db.session import session_scope
from app.schemas.squad import SquadStateRead
from app.services.fpl_api import FPLAPIClient, FPLAPIError
from app.services.squad import SquadSyncResult, SquadSyncService

logger = logging.getLogger(__name__)
router = APIRouter()

_squad_sync = SquadSyncService()


class SquadSyncResultOut(BaseModel):
    """API-friendly view of `SquadSyncResult`."""

    gameweek: int
    state_created: bool
    players_created: int
    players_updated: int
    players_removed: int
    free_transfers: int
    chips_available: list[str]
    chip_played: str | None
    bank_balance: int
    squad_value: int

    @classmethod
    def from_result(cls, result: SquadSyncResult) -> "SquadSyncResultOut":
        return cls(**result.__dict__)


@router.post("/sync/{gameweek}", response_model=SquadSyncResultOut)
async def sync_my_squad(gameweek: int) -> SquadSyncResultOut:
    """Fetch this gameweek's picks for `settings.fpl_team_id` and persist them.

    Requires `FPL_TEAM_ID` to be set in `.env` (see the project README for
    where to find your own team/entry id in the FPL site's URL). Run this
    once per gameweek, ideally right after the gameweek's deadline, so the
    transfer optimizer (Phase 6) always has an up-to-date squad to plan
    from.
    """
    if settings.fpl_team_id is None:
        raise HTTPException(
            status_code=422,
            detail="FPL_TEAM_ID is not configured - set it in .env before syncing a squad.",
        )

    async with FPLAPIClient() as client:
        try:
            picks_payload = await client.get_entry_event_picks(
                entry_id=settings.fpl_team_id, event_id=gameweek
            )
        except FPLAPIError as exc:
            logger.exception("Squad sync: FPL API fetch failed for gw %s", gameweek)
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    with session_scope() as db:
        result = _squad_sync.sync_from_fpl_payloads(db, gameweek=gameweek, picks_payload=picks_payload)

    return SquadSyncResultOut.from_result(result)


@router.get("/{gameweek}", response_model=SquadStateRead)
async def read_squad_state(gameweek: int) -> SquadStateRead:
    """Read the most recently synced squad state at or before `gameweek`.

    Uses "at or before" (the same lookup the Phase 6 optimizer relies on)
    rather than an exact match, since a squad snapshot stays valid until
    the next gameweek's picks are synced.
    """
    from sqlalchemy import select

    from app.models.squad import SquadState

    with session_scope() as db:
        state = (
            db.execute(
                select(SquadState)
                .where(SquadState.gameweek <= gameweek)
                .order_by(SquadState.gameweek.desc())
            )
            .scalars()
            .first()
        )
        if state is None:
            raise HTTPException(
                status_code=404,
                detail=f"No squad state found at or before gameweek {gameweek}.",
            )
        return SquadStateRead.model_validate(state)
