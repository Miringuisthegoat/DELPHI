"""
Phase 6: plain dataclasses returned by `TransferOptimizerService`.

Kept free of SQLAlchemy/OR-Tools so the API schema layer (`app/schemas/
optimization.py`) and the CLI (`scripts/optimize_transfers.py`) can both
consume the same shapes without importing solver internals.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PlayerMove:
    """One player entering or leaving the squad in a transfer option."""

    player_id: int
    web_name: str
    position: str
    price_millions: float
    predicted_points: float


@dataclass
class TransferOption:
    """One evaluated "what if I made exactly N transfers" scenario."""

    transfers: int
    players_out: list[PlayerMove]
    players_in: list[PlayerMove]
    points_gained: float
    """Sum of predicted points (over the requested horizon) of players bought in."""
    points_lost: float
    """Sum of predicted points (over the requested horizon) of players sold."""
    hit_cost: int
    """Points deducted for transfers beyond `free_transfers` (0, 4, 8, ...)."""
    net_expected_gain: float
    """points_gained - points_lost - hit_cost. The number every option is ranked on."""
    bank_after: float
    feasible: bool
    reasoning: str


@dataclass
class OptimizationResult:
    """Every transfer-count option considered, plus the overall recommendation."""

    gameweek: int
    horizon: int
    free_transfers: int
    options: list[TransferOption] = field(default_factory=list)
    recommended: TransferOption | None = None
