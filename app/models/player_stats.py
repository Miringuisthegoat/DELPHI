"""Per-gameweek player statistics.

This is the core historical time series the prediction engine trains on.
One row is written per player per gameweek and is never overwritten, so the
full season (and future seasons) of history accumulates over time.

Phase 13: adds the 2025-26-rules "defensive contribution" scoring fields
(clearances_blocks_interceptions, defensive_contribution, recoveries,
tackles) - a new points source FPL introduced for DEF/MID players who
cross a per-gameweek CBIT/tackle threshold. Absent from every earlier
season's data, these are nullable-with-default-0 so old rows (and any
source that doesn't provide them) remain valid without a migration
backfill.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Float, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.player import Player


class PlayerGameweekStats(TimestampMixin, Base):
    """Actual recorded performance for one player in one gameweek."""

    __tablename__ = "player_gameweek_stats"
    __table_args__ = (
        UniqueConstraint("player_id", "gameweek", name="uq_player_gameweek"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
    gameweek: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    fixture_id: Mapped[int | None] = mapped_column(
        ForeignKey("fixtures.id"), nullable=True
    )

    # --- Playing time -------------------------------------------------------
    minutes: Mapped[int] = mapped_column(Integer, default=0)
    started: Mapped[bool] = mapped_column(default=False)

    # --- Attacking returns ---------------------------------------------------
    goals_scored: Mapped[int] = mapped_column(Integer, default=0)
    assists: Mapped[int] = mapped_column(Integer, default=0)
    expected_goals: Mapped[float] = mapped_column(Float, default=0.0)
    expected_assists: Mapped[float] = mapped_column(Float, default=0.0)
    expected_goal_involvements: Mapped[float] = mapped_column(Float, default=0.0)

    # --- Defensive returns ---------------------------------------------------
    clean_sheets: Mapped[int] = mapped_column(Integer, default=0)
    goals_conceded: Mapped[int] = mapped_column(Integer, default=0)
    expected_goals_conceded: Mapped[float] = mapped_column(Float, default=0.0)
    saves: Mapped[int] = mapped_column(Integer, default=0)
    own_goals: Mapped[int] = mapped_column(Integer, default=0)
    penalties_saved: Mapped[int] = mapped_column(Integer, default=0)

    # --- Phase 13: defensive contribution (2025-26 scoring rules) --------------
    clearances_blocks_interceptions: Mapped[int] = mapped_column(Integer, default=0)
    tackles: Mapped[int] = mapped_column(Integer, default=0)
    recoveries: Mapped[int] = mapped_column(Integer, default=0)
    defensive_contribution: Mapped[int] = mapped_column(
        Integer,
        default=0,
        doc=(
            "FPL's own points-eligibility indicator for the CBIT/tackle "
            "threshold (DEF: 10+, MID/FWD: 12+ combined actions = 2 bonus "
            "pts). Stored as provided by the source rather than "
            "recomputed, since FPL may tune the threshold between seasons."
        ),
    )

    # --- Discipline -----------------------------------------------------------
    yellow_cards: Mapped[int] = mapped_column(Integer, default=0)
    red_cards: Mapped[int] = mapped_column(Integer, default=0)
    penalties_missed: Mapped[int] = mapped_column(Integer, default=0)

    # --- FPL scoring outputs ---------------------------------------------------
    bonus: Mapped[int] = mapped_column(Integer, default=0)
    bps: Mapped[int] = mapped_column(Integer, default=0, doc="Bonus Points System score.")
    total_points: Mapped[int] = mapped_column(Integer, default=0)

    # --- Market / form context at the time of the gameweek ----------------------
    price_at_gameweek: Mapped[int] = mapped_column(
        Integer, default=0, doc="Price in tenths of a million, as-of this gameweek."
    )
    ownership_percent: Mapped[float] = mapped_column(Float, default=0.0)
    form: Mapped[float] = mapped_column(Float, default=0.0)
    ict_index: Mapped[float] = mapped_column(Float, default=0.0)
    influence: Mapped[float] = mapped_column(Float, default=0.0)
    creativity: Mapped[float] = mapped_column(Float, default=0.0)
    threat: Mapped[float] = mapped_column(Float, default=0.0)

    player: Mapped["Player"] = relationship(back_populates="gameweek_stats")

    def __repr__(self) -> str:  # pragma: no cover - repr only
        return (
            f"<PlayerGameweekStats player_id={self.player_id} "
            f"gw={self.gameweek} points={self.total_points}>"
        )
