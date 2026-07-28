"""ORM models package.

Importing this package registers every model on `Base.metadata`, which is
required before calling `Base.metadata.create_all()`.
"""

from app.models.enums import ChipType, InjuryStatus, Position, TransferDecision
from app.models.fixture import Fixture
from app.models.player import Player
from app.models.player_stats import PlayerGameweekStats
from app.models.prediction import Prediction
from app.models.squad import SquadPlayer, SquadState
from app.models.team import Team
from app.models.transfer import TransferHistory

__all__ = [
    "ChipType",
    "InjuryStatus",
    "Position",
    "TransferDecision",
    "Fixture",
    "Player",
    "PlayerGameweekStats",
    "Prediction",
    "SquadPlayer",
    "SquadState",
    "Team",
    "TransferHistory",
]
