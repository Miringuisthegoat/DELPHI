"""Pydantic schemas for squad state and squad membership."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class SquadPlayerRead(BaseModel):
    """A single player's role within a squad snapshot."""

    model_config = ConfigDict(from_attributes=True)

    player_id: int
    purchase_price: int
    selling_price: int
    is_starting: bool
    bench_position: int | None = None
    is_captain: bool
    is_vice_captain: bool


class SquadStateRead(BaseModel):
    """Full snapshot of the user's squad for a given gameweek."""

    model_config = ConfigDict(from_attributes=True)

    gameweek: int
    bank_balance: int
    squad_value: int
    free_transfers: int
    chips_available: list[str]
    chip_played: str | None = None
    overall_rank: int | None = None
    total_points: int
    players: list[SquadPlayerRead] = []

    @property
    def bank_millions(self) -> float:
        return self.bank_balance / 10

    @property
    def squad_value_millions(self) -> float:
        return self.squad_value / 10
