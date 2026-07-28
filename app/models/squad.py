""""My Squad" models.

The application's core philosophy is to always start from the user's
*existing* squad rather than rebuilding from scratch. `SquadState` stores a
snapshot of the squad at the start of each gameweek (bank, chips, free
transfers, etc.) and `SquadPlayer` records which 15 players made up that
snapshot along with their purchase/selling prices and role in the XI.

Keeping one `SquadState` row per gameweek (rather than mutating a single
"current squad" row) gives the app a full history of team decisions across
the season for free.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.player import Player


class SquadState(TimestampMixin, Base):
    """Snapshot of the user's overall squad situation for one gameweek."""

    __tablename__ = "squad_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    gameweek: Mapped[int] = mapped_column(Integer, nullable=False, unique=True, index=True)

    bank_balance: Mapped[int] = mapped_column(
        Integer, default=0, doc="Money in the bank, in tenths of a million."
    )
    squad_value: Mapped[int] = mapped_column(
        Integer, default=0, doc="Total squad value, in tenths of a million."
    )
    free_transfers: Mapped[int] = mapped_column(Integer, default=1)

    chips_available: Mapped[list[str]] = mapped_column(
        JSON, default=list, doc="Chip identifiers (see ChipType) not yet used."
    )
    chip_played: Mapped[str | None] = mapped_column(String(32), nullable=True)

    overall_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_points: Mapped[int] = mapped_column(Integer, default=0)

    players: Mapped[list["SquadPlayer"]] = relationship(
        back_populates="squad_state", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover - repr only
        return (
            f"<SquadState gw={self.gameweek} bank={self.bank_balance} "
            f"free_transfers={self.free_transfers}>"
        )


class SquadPlayer(TimestampMixin, Base):
    """One of the 15 players in the squad for a given `SquadState`."""

    __tablename__ = "squad_players"
    __table_args__ = (
        UniqueConstraint("squad_state_id", "player_id", name="uq_squad_state_player"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    squad_state_id: Mapped[int] = mapped_column(
        ForeignKey("squad_states.id"), nullable=False
    )
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)

    purchase_price: Mapped[int] = mapped_column(
        Integer, doc="Price paid, in tenths of a million."
    )
    selling_price: Mapped[int] = mapped_column(
        Integer, doc="Current sell-on value, in tenths of a million (accounts for FPL's 50% profit rule)."
    )

    is_starting: Mapped[bool] = mapped_column(Boolean, default=True)
    bench_position: Mapped[int | None] = mapped_column(
        Integer, nullable=True, doc="1-4 for bench order; null when starting."
    )
    is_captain: Mapped[bool] = mapped_column(Boolean, default=False)
    is_vice_captain: Mapped[bool] = mapped_column(Boolean, default=False)

    squad_state: Mapped["SquadState"] = relationship(back_populates="players")
    player: Mapped["Player"] = relationship(back_populates="squad_entries")

    def __repr__(self) -> str:  # pragma: no cover - repr only
        return (
            f"<SquadPlayer squad_state_id={self.squad_state_id} "
            f"player_id={self.player_id} starting={self.is_starting}>"
        )
