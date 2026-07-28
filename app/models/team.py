"""Premier League team (club) model."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.fixture import Fixture
    from app.models.player import Player


class Team(TimestampMixin, Base):
    """A Premier League club.

    Mirrors the `teams` array of the FPL `bootstrap-static` endpoint.
    """

    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    """Uses the official FPL team id (not an autoincrement surrogate),
    so foreign keys line up directly with data pulled from the API."""

    name: Mapped[str] = mapped_column(String(64), nullable=False)
    short_name: Mapped[str] = mapped_column(String(8), nullable=False)

    # FPL's own 1-5 style strength ratings, split home/away and
    # attack/defence, as returned by bootstrap-static.
    strength_overall_home: Mapped[int] = mapped_column(Integer, default=0)
    strength_overall_away: Mapped[int] = mapped_column(Integer, default=0)
    strength_attack_home: Mapped[int] = mapped_column(Integer, default=0)
    strength_attack_away: Mapped[int] = mapped_column(Integer, default=0)
    strength_defence_home: Mapped[int] = mapped_column(Integer, default=0)
    strength_defence_away: Mapped[int] = mapped_column(Integer, default=0)

    # Convenience aggregate fields used widely by the prediction engine.
    strength_attack: Mapped[int] = mapped_column(Integer, default=0)
    strength_defence: Mapped[int] = mapped_column(Integer, default=0)

    players: Mapped[list["Player"]] = relationship(back_populates="team")
    home_fixtures: Mapped[list["Fixture"]] = relationship(
        back_populates="home_team",
        foreign_keys="Fixture.home_team_id",
    )
    away_fixtures: Mapped[list["Fixture"]] = relationship(
        back_populates="away_team",
        foreign_keys="Fixture.away_team_id",
    )

    def __repr__(self) -> str:  # pragma: no cover - repr only
        return f"<Team id={self.id} name={self.name!r}>"
