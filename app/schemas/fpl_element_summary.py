"""Typed schemas for the ``/element-summary/{element_id}/`` endpoint.

This is the endpoint we'll call once per player of interest (not for
all 600+ players every gameweek — see the client's docstring for the
concurrency/backoff approach this requires) to get that player's
gameweek-by-gameweek history plus upcoming fixtures.

Phase 13: adds the four "defensive contribution" fields FPL introduced
for the 2025-26 season (``clearances_blocks_interceptions``, ``tackles``,
``recoveries``, ``defensive_contribution``). All default to 0 so history
rows from before this rule existed (or any payload that omits them)
still validate.
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
    expected_goals: str = "0.0"
    expected_assists: str = "0.0"
    expected_goal_involvements: str = "0.0"
    expected_goals_conceded: str = "0.0"
    starts: int | None = None
    """1 if the player started the match, 0 otherwise. Newer FPL seasons
    include this explicitly; older ones don't, so it's optional and the
    ingestion layer falls back to a minutes-played heuristic when absent."""
    value: int
    """Player price at the time of this gameweek, in tenths of a million."""

    transfers_balance: int = 0
    selected: int = 0
    transfers_in: int = 0
    transfers_out: int = 0

    # --- Phase 13: defensive contribution (2025-26+ scoring rules) -------
    clearances_blocks_interceptions: int = 0
    tackles: int = 0
    recoveries: int = 0
    defensive_contribution: int = 0
    """FPL's own indicator of whether this gameweek crossed the CBIT/
    tackle threshold for bonus defensive points. 0 for gameweeks before
    the rule existed - the FPL API itself won't send this field for old
    seasons, so the default correctly reflects "not applicable"."""


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
