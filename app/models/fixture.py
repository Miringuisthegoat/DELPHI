"""Fixture model: a single Premier League match tied to an FPL gameweek."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.team import Team


class Fixture(TimestampMixin, Base):
    """A single fixture, mirroring the FPL `fixtures` endpoint.

    Difficulty ratings are stored separately for the home and away side
    because FPL's own Fixture Difficulty Rating (FDR) is asymmetric (a
    fixture can be "easy" for the home team and "hard" for the away team
    simultaneously).
    """

    __tablename__ = "fixtures"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    """The official FPL fixture id."""

    gameweek: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True, doc="Null for postponed/unscheduled fixtures."
    )

    home_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    away_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)

    home_difficulty: Mapped[int] = mapped_column(
        Integer, doc="FPL FDR (1=easiest, 5=hardest) for the home team."
    )
    away_difficulty: Mapped[int] = mapped_column(
        Integer, doc="FPL FDR (1=easiest, 5=hardest) for the away team."
    )

    kickoff_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    finished: Mapped[bool] = mapped_column(Boolean, default=False)
    home_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_score: Mapped[int | None] = mapped_column(Integer, nullable=True)

    home_team: Mapped["Team"] = relationship(
        back_populates="home_fixtures", foreign_keys=[home_team_id]
    )
    away_team: Mapped["Team"] = relationship(
        back_populates="away_fixtures", foreign_keys=[away_team_id]
    )

    def difficulty_for(self, team_id: int) -> int:
        """Return the fixture difficulty rating from the given team's perspective."""
        if team_id == self.home_team_id:
            return self.home_difficulty
        if team_id == self.away_team_id:
            return self.away_difficulty
        raise ValueError(f"Team {team_id} is not part of fixture {self.id}")

    def __repr__(self) -> str:  # pragma: no cover - repr only
        return (
            f"<Fixture id={self.id} gw={self.gameweek} "
            f"home={self.home_team_id} away={self.away_team_id}>"
        )
