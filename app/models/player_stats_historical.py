"""Phase 12: per-gameweek player statistics from PRIOR seasons.

Sourced from the community-maintained vaastav/Fantasy-Premier-League repo
(https://github.com/vaastav/Fantasy-Premier-League) rather than the live
FPL API, which only ever exposes the current season. Deliberately a
separate table from `PlayerGameweekStats` (not a shared one with a
`season` column bolted on) because:

* Historical rows are keyed by name, not by the current season's FPL
  `element` id (which is not stable across seasons - see PHASE_12_README).
  `matched_player_id` is nullable and only set when `name_matcher` finds a
  confident match to a current `Player` row.
* Historical data is immutable (past seasons never change) and ingested
  once via a manual CLI run, never touched by the weekly pipeline - having
  its own table keeps that distinction structurally obvious rather than
  relying on every query to remember to filter by season/source.

Phase 13: adds the four "defensive contribution" columns FPL introduced
for the 2025-26 season (clearances_blocks_interceptions, tackles,
recoveries, defensive_contribution). Earlier seasons' CSVs simply don't
have these columns - `resolve_columns`/`map_gw_row` in
`app.services.historical.mappers` default them to 0 for any season where
they're absent, same pattern as every other optional field here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.player import Player


class HistoricalPlayerGameweekStats(TimestampMixin, Base):
    """One player's actual performance in one gameweek of a PAST season."""

    __tablename__ = "player_gameweek_stats_historical"
    __table_args__ = (
        UniqueConstraint(
            "season", "source_name", "gameweek", name="uq_historical_season_name_gw"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    season: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    """e.g. '2023-24', matching the vaastav repo's folder naming."""

    gameweek: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    # --- Identity as it appeared in that season (names, not live ids) -------
    source_name: Mapped[str] = mapped_column(String(128), nullable=False)
    """Raw `name` field from merged_gw.csv for that season, kept verbatim
    for auditability/debugging of the name-matching step."""
    position: Mapped[str] = mapped_column(String(8), default="MID")
    team_name: Mapped[str] = mapped_column(String(64), default="")

    matched_player_id: Mapped[int | None] = mapped_column(
        ForeignKey("players.id"), nullable=True, index=True
    )
    """Set by `name_matcher` when this historical player is confidently
    resolved to a current `Player` row. NULL means either the player has
    left the league/dataset, or matching wasn't confident enough - such
    rows still count toward general model training but never toward any
    specific player's career-prior features."""
    match_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    match_method: Mapped[str] = mapped_column(String(32), default="unmatched")
    """One of 'exact_web_name', 'exact_full_name', 'fuzzy', 'unmatched'."""

    # --- Playing time ---------------------------------------------------------
    minutes: Mapped[int] = mapped_column(Integer, default=0)

    # --- Attacking returns ------------------------------------------------------
    goals_scored: Mapped[int] = mapped_column(Integer, default=0)
    assists: Mapped[int] = mapped_column(Integer, default=0)
    expected_goals: Mapped[float] = mapped_column(Float, default=0.0)
    expected_assists: Mapped[float] = mapped_column(Float, default=0.0)

    # --- Defensive returns --------------------------------------------------------
    clean_sheets: Mapped[int] = mapped_column(Integer, default=0)
    goals_conceded: Mapped[int] = mapped_column(Integer, default=0)
    saves: Mapped[int] = mapped_column(Integer, default=0)
    own_goals: Mapped[int] = mapped_column(Integer, default=0)
    penalties_saved: Mapped[int] = mapped_column(Integer, default=0)

    # --- Phase 13: defensive contribution (2025-26+ scoring rules) -------------
    clearances_blocks_interceptions: Mapped[int] = mapped_column(Integer, default=0)
    tackles: Mapped[int] = mapped_column(Integer, default=0)
    recoveries: Mapped[int] = mapped_column(Integer, default=0)
    defensive_contribution: Mapped[int] = mapped_column(Integer, default=0)
    """0 for every season before 2025-26 (column didn't exist yet) - not
    a data-quality gap, just historically accurate: the scoring rule
    itself didn't exist."""

    # --- Discipline --------------------------------------------------------------
    yellow_cards: Mapped[int] = mapped_column(Integer, default=0)
    red_cards: Mapped[int] = mapped_column(Integer, default=0)
    penalties_missed: Mapped[int] = mapped_column(Integer, default=0)

    # --- FPL scoring outputs ---------------------------------------------------------
    bonus: Mapped[int] = mapped_column(Integer, default=0)
    bps: Mapped[int] = mapped_column(Integer, default=0)
    total_points: Mapped[int] = mapped_column(Integer, default=0)

    # --- Market / form context at the time -------------------------------------------
    price_at_gameweek: Mapped[int] = mapped_column(Integer, default=0)
    ict_index: Mapped[float] = mapped_column(Float, default=0.0)
    influence: Mapped[float] = mapped_column(Float, default=0.0)
    creativity: Mapped[float] = mapped_column(Float, default=0.0)
    threat: Mapped[float] = mapped_column(Float, default=0.0)

    # --- vaastav's own expected-points column, kept for later comparison -----------
    source_xp: Mapped[float | None] = mapped_column(Float, nullable=True)
    """Their own 'xP' column, if present in that season's CSV. Not used by
    DELPHI's model; retained so a future evaluation can compare DELPHI's
    predictions against a naive external baseline."""

    matched_player: Mapped["Player | None"] = relationship()

    def __repr__(self) -> str:  # pragma: no cover - repr only
        return (
            f"<HistoricalPlayerGameweekStats season={self.season} "
            f"name={self.source_name!r} gw={self.gameweek} points={self.total_points}>"
        )
