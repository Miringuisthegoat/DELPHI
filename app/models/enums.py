"""Shared enumerations used across ORM models and business logic.

Keeping these centralised avoids "magic strings" scattered through the
codebase and gives type-checkers/IDEs something concrete to validate
against.
"""

from __future__ import annotations

import enum


class Position(str, enum.Enum):
    """Official FPL player positions."""

    GKP = "GKP"
    DEF = "DEF"
    MID = "MID"
    FWD = "FWD"


class ChipType(str, enum.Enum):
    """Official FPL chips."""

    WILDCARD = "wildcard"
    FREE_HIT = "free_hit"
    BENCH_BOOST = "bench_boost"
    TRIPLE_CAPTAIN = "triple_captain"


class InjuryStatus(str, enum.Enum):
    """FPL's own player availability status codes.

    Mirrors the `status` field returned by the bootstrap-static endpoint:
    a = available, d = doubtful, i = injured, s = suspended, u = unavailable.
    """

    AVAILABLE = "a"
    DOUBTFUL = "d"
    INJURED = "i"
    SUSPENDED = "s"
    UNAVAILABLE = "u"


class TransferDecision(str, enum.Enum):
    """Outcome categories used by the transfer optimizer / history log."""

    NO_TRANSFER = "no_transfer"
    ONE_TRANSFER = "one_transfer"
    TWO_TRANSFERS = "two_transfers"
    HIT_TRANSFER = "hit_transfer"
    CHIP_PLAYED = "chip_played"
