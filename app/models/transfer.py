"""Transfer history model.

Records every transfer the app has ever recommended or the user has made,
along with the *expected* gain (from the optimizer) and the *actual* gain
(once the gameweek is played). This is the raw material for evaluating how
good the transfer optimizer actually is over time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Enum, Float, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.enums import TransferDecision

if TYPE_CHECKING:
    from app.models.player import Player


class TransferHistory(TimestampMixin, Base):
    """A single player-out / player-in transfer made in a given gameweek."""

    __tablename__ = "transfer_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    gameweek: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    player_out_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
    player_in_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)

    decision_type: Mapped[TransferDecision] = mapped_column(
        Enum(TransferDecision), default=TransferDecision.ONE_TRANSFER
    )

    was_hit: Mapped[bool] = mapped_column(
        Boolean, default=False, doc="True if this transfer cost -4 points (or more)."
    )
    points_cost: Mapped[int] = mapped_column(
        Integer, default=0, doc="Total point deduction incurred (0, 4, 8, ...)."
    )

    expected_gain: Mapped[float] = mapped_column(
        Float, default=0.0, doc="Model-predicted point gain over the planning horizon."
    )
    actual_gain: Mapped[float | None] = mapped_column(
        Float, nullable=True, doc="Realised point gain, filled in after the fact."
    )

    reasoning: Mapped[str | None] = mapped_column(
        Text, nullable=True, doc="Human-readable explanation shown in the report."
    )

    player_out: Mapped["Player"] = relationship(
        back_populates="transfers_out", foreign_keys=[player_out_id]
    )
    player_in: Mapped["Player"] = relationship(
        back_populates="transfers_in", foreign_keys=[player_in_id]
    )

    def __repr__(self) -> str:  # pragma: no cover - repr only
        return (
            f"<TransferHistory gw={self.gameweek} "
            f"out={self.player_out_id} in={self.player_in_id}>"
        )
