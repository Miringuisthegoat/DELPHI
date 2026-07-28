"""Typed schemas for the ``/element-summary/{element_id}/`` endpoint.

This is the endpoint we'll call once per player of interest (not for
all 600+ players every gameweek — see the client's docstring for the
concurrency/backoff approach this requires) to get that player's
gameweek-by-gameweek history plus upcoming fixtures.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class FPLElementHistory(BaseModel):
    """One row of a player's past-gameweek performance this season."""

    model_config = ConfigDict(extra="ignore")

    element: int
    fixture: int
    opponent_team: int
    total_points: int
    was_home: bool
    kickoff_time: datetime | None
    round: int
    """The gameweek number."""

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
    value: int
    """Player price at the time of this gameweek, in tenths of a million."""

    transfers_balance: int = 0
    selected: int = 0
    transfers_in: int = 0
    transfers_out: int = 0


class FPLElementHistoryPast(BaseModel):
    """A summary row for one *previous* season (not the current one)."""

    model_config = ConfigDict(extra="ignore")

    season_name: str
    element_code: int
    total_points: int
    minutes: int = 0
    goals_scored: int = 0
    assists: int = 0


class FPLElementFixture(BaseModel):
    """One of a player's upcoming (not yet played) fixtures."""

    model_config = ConfigDict(extra="ignore")

    id: int
    event: int | None
    is_home: bool
    difficulty: int
    team_h: int
    team_a: int
    kickoff_time: datetime | None


class FPLElementSummary(BaseModel):
    """Top-level response shape of ``/element-summary/{element_id}/``."""

    model_config = ConfigDict(extra="ignore")

    fixtures: list[FPLElementFixture]
    history: list[FPLElementHistory]
    history_past: list[FPLElementHistoryPast]
