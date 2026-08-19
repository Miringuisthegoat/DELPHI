"""Typed schemas for the ``/event/{event_id}/live/`` endpoint.

Used for near-real-time stats during and shortly after a live
gameweek (bonus points, in particular, are often provisional for a
few hours after matches finish — ``FPLLiveStats.bps`` and
``bonus`` should be treated as provisional until the fixture's
``finished`` flag, from the fixtures endpoint, is true).

Phase 13: adds the four "defensive contribution" fields FPL introduced
for the 2025-26 season, mirroring the same addition to
`app.schemas.fpl_element_summary.FPLElementHistory`.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class FPLLiveStats(BaseModel):
    """A single player's stat line for one live/completed gameweek."""

    model_config = ConfigDict(extra="ignore")

    minutes: int = 0
    goals_scored: int = 0
    assists: int = 0
    clean_sheets: int = 0
    goals_conceded: int = 0
    own_goals: int = 0
    penalties_saved: int = 0
    penalties_missed: int = 0
    yellow_cards: int = 0
    red_cards: int = 0
    saves: int = 0
    bonus: int = 0
    bps: int = 0
    influence: str = "0.0"
    creativity: str = "0.0"
    threat: str = "0.0"
    ict_index: str = "0.0"
    total_points: int = 0
    in_dreamteam: bool = False

    # --- Phase 13: defensive contribution (2025-26+ scoring rules) -------
    clearances_blocks_interceptions: int = 0
    tackles: int = 0
    recoveries: int = 0
    defensive_contribution: int = 0


class FPLLiveElement(BaseModel):
    """One player's entry in the live gameweek payload."""

    model_config = ConfigDict(extra="ignore")

    id: int
    stats: FPLLiveStats


class FPLEventLive(BaseModel):
    """Top-level response shape of ``/event/{event_id}/live/``."""

    model_config = ConfigDict(extra="ignore")

    elements: list[FPLLiveElement]
