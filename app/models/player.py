"""Player (FPL "element") model."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Enum, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.enums import InjuryStatus, Position

if TYPE_CHECKING:
    from app.models.player_stats import PlayerGameweekStats
    from app.models.prediction import Prediction
    from app.models.squad import SquadPlayer
    from app.models.team import Team
    from app.models.transfer import TransferHistory


class Player(TimestampMixin, Base):
    """A single player ("element" in FPL terminology).

    Static/slow-changing attributes live here. Fast-changing per-gameweek
    numbers (form, points, minutes, etc.) live in `PlayerGameweekStats` so
    we retain a full time series rather than overwriting history.
    """

    __tablename__ = "players"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    """The official FPL element id."""

    first_name: Mapped[str] = mapped_column(String(64), nullable=False)
    second_name: Mapped[str] = mapped_column(String(64), nullable=False)
    web_name: Mapped[str] = mapped_column(String(64), nullable=False)
    """Short display name as shown on the FPL site (e.g. "Salah")."""

    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    position: Mapped[Position] = mapped_column(Enum(Position), nullable=False)

    # --- Current market data --------------------------------------------
    # Prices are stored in tenths of a million, matching the raw FPL API
    # (e.g. 125 == £12.5m), to avoid floating point rounding issues.
    now_cost: Mapped[int] = mapped_column(Integer, nullable=False)
    ownership_percent: Mapped[float] = mapped_column(Float, default=0.0)
    price_trend: Mapped[float] = mapped_column(
        Float, default=0.0, doc="Net transfers-in minus transfers-out signal."
    )

    # --- Availability ------------------------------------------------------
    status: Mapped[InjuryStatus] = mapped_column(
        Enum(InjuryStatus), default=InjuryStatus.AVAILABLE
    )
    chance_of_playing_next_round: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    news: Mapped[str | None] = mapped_column(String(512), nullable=True)

    is_active: Mapped[bool] = mapped_column(
        default=True, doc="False once a player leaves the league/dataset."
    )

    # --- Relationships -----------------------------------------------------
    team: Mapped["Team"] = relationship(back_populates="players")
    gameweek_stats: Mapped[list["PlayerGameweekStats"]] = relationship(
        back_populates="player", cascade="all, delete-orphan"
    )
    predictions: Mapped[list["Prediction"]] = relationship(
        back_populates="player", cascade="all, delete-orphan"
    )
    squad_entries: Mapped[list["SquadPlayer"]] = relationship(back_populates="player")
    transfers_in: Mapped[list["TransferHistory"]] = relationship(
        back_populates="player_in",
        foreign_keys="TransferHistory.player_in_id",
    )
    transfers_out: Mapped[list["TransferHistory"]] = relationship(
        back_populates="player_out",
        foreign_keys="TransferHistory.player_out_id",
    )

    @property
    def full_name(self) -> str:
        """Convenience full name, e.g. 'Mohamed Salah'."""
        return f"{self.first_name} {self.second_name}"

    @property
    def price_millions(self) -> float:
        """Current price expressed in millions (e.g. 12.5)."""
        return self.now_cost / 10

    def __repr__(self) -> str:  # pragma: no cover - repr only
        return (
            f"<Player id={self.id} name={self.web_name!r} "
            f"pos={self.position.value} price={self.price_millions}>"
        )
