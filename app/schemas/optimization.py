"""API-facing schemas for the Phase 6 transfer optimizer endpoint."""

from __future__ import annotations

from pydantic import BaseModel

from app.optimization.models import OptimizationResult, PlayerMove, TransferOption


class PlayerMoveOut(BaseModel):
    player_id: int
    web_name: str
    position: str
    price_millions: float
    predicted_points: float

    @classmethod
    def from_move(cls, move: PlayerMove) -> "PlayerMoveOut":
        return cls(**move.__dict__)


class TransferOptionOut(BaseModel):
    transfers: int
    players_out: list[PlayerMoveOut]
    players_in: list[PlayerMoveOut]
    points_gained: float
    points_lost: float
    hit_cost: int
    net_expected_gain: float
    bank_after: float
    feasible: bool
    reasoning: str

    @classmethod
    def from_option(cls, option: TransferOption) -> "TransferOptionOut":
        return cls(
            transfers=option.transfers,
            players_out=[PlayerMoveOut.from_move(p) for p in option.players_out],
            players_in=[PlayerMoveOut.from_move(p) for p in option.players_in],
            points_gained=option.points_gained,
            points_lost=option.points_lost,
            hit_cost=option.hit_cost,
            net_expected_gain=option.net_expected_gain,
            bank_after=option.bank_after,
            feasible=option.feasible,
            reasoning=option.reasoning,
        )


class OptimizationResponse(BaseModel):
    """Response for `POST /optimization/optimize/{gameweek}`."""

    gameweek: int
    horizon: int
    free_transfers: int
    options: list[TransferOptionOut]
    recommended: TransferOptionOut

    @classmethod
    def from_result(cls, result: OptimizationResult) -> "OptimizationResponse":
        return cls(
            gameweek=result.gameweek,
            horizon=result.horizon,
            free_transfers=result.free_transfers,
            options=[TransferOptionOut.from_option(o) for o in result.options],
            recommended=TransferOptionOut.from_option(result.recommended),
        )
