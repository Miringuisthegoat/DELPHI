"""Typed schema for the ``/fixtures/`` endpoint."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class FPLFixture(BaseModel):
    """A single Premier League fixture, with FPL's own difficulty rating.

    ``team_h_difficulty`` / ``team_a_difficulty`` are FPL's official
    1 (easiest) to 5 (hardest) fixture-difficulty ratings, provided
    per side since difficulty differs for the home vs away team.
    """

    model_config = ConfigDict(extra="ignore")

    id: int
    event: int | None
    """The gameweek this fixture belongs to. Can be null for fixtures
    not yet scheduled into a gameweek (e.g. postponed matches)."""

    kickoff_time: datetime | None
    finished: bool
    finished_provisional: bool = False
    started: bool | None = None

    team_h: int
    team_a: int
    team_h_score: int | None = None
    team_a_score: int | None = None
    team_h_difficulty: int
    team_a_difficulty: int

    minutes: int = 0
    provisional_start_time: bool = False
    pulse_id: int | None = None
