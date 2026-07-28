"""Prediction model.

Every prediction the ML engine ever makes is stored here, along with the
actual outcome once the gameweek is played. This is what powers the
"learning system": comparing `predicted_points` to `actual_points` lets the
app measure and gradually reduce `prediction_error`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.player import Player


class Prediction(TimestampMixin, Base):
    """A single model prediction for one player / gameweek / horizon."""

    __tablename__ = "predictions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
    gameweek: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True, doc="The gameweek being predicted."
    )
    horizon: Mapped[int] = mapped_column(
        Integer,
        default=1,
        doc="How many gameweeks ahead this prediction was made for (1, 3, or 5).",
    )

    model_name: Mapped[str] = mapped_column(
        String(64), default="random_forest_v1", doc="Identifier of the model used."
    )
    model_version: Mapped[str] = mapped_column(String(32), default="1.0.0")

    predicted_points: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[float] = mapped_column(
        Float, default=0.0, doc="Model confidence, expressed as 0-1 or 0-100."
    )

    actual_points: Mapped[float | None] = mapped_column(
        Float, nullable=True, doc="Filled in once the gameweek has been played."
    )
    prediction_error: Mapped[float | None] = mapped_column(
        Float, nullable=True, doc="actual_points - predicted_points, once known."
    )

    player: Mapped["Player"] = relationship(back_populates="predictions")

    def record_actual(self, actual_points: float) -> None:
        """Populate the actual outcome and derive the prediction error.

        Args:
            actual_points: The points the player actually scored.
        """
        self.actual_points = actual_points
        self.prediction_error = actual_points - self.predicted_points

    def __repr__(self) -> str:  # pragma: no cover - repr only
        return (
            f"<Prediction player_id={self.player_id} gw={self.gameweek} "
            f"horizon={self.horizon} predicted={self.predicted_points}>"
        )
