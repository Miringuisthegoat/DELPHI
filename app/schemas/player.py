"""Pydantic schemas for `Player` and `Team`.

These are the shapes used at API/service boundaries (FastAPI routes,
external clients). ORM models are never returned directly from endpoints;
they are converted to these schemas first, keeping the DB schema free to
evolve independently of the public API contract.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from app.models.enums import InjuryStatus, Position


class TeamRead(BaseModel):
    """Read-only representation of a Premier League club."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    short_name: str
    strength_attack: int
    strength_defence: int


class PlayerRead(BaseModel):
    """Read-only representation of a player, as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    web_name: str
    first_name: str
    second_name: str
    position: Position
    team_id: int
    now_cost: int
    ownership_percent: float
    status: InjuryStatus
    chance_of_playing_next_round: int | None = None
    news: str | None = None

    @property
    def price_millions(self) -> float:
        return self.now_cost / 10


class PlayerCreate(BaseModel):
    """Payload used when upserting a player from the FPL API response."""

    id: int
    web_name: str
    first_name: str
    second_name: str
    position: Position
    team_id: int
    now_cost: int
    ownership_percent: float = 0.0
    status: InjuryStatus = InjuryStatus.AVAILABLE
    chance_of_playing_next_round: int | None = None
    news: str | None = None
