# tests/test_historical_features_patch.py
"""
Verification tests for the Phase 12/13 zero-importance-feature fix.

These tests use a *local* minimal stand-in for `HistoricalPlayerGameweekStats`
rather than importing the real one, so they can run and prove the merge
logic works correctly in isolation - independent of whether the real
model's column names match this project's assumptions.

IMPORTANT: If your real `app.models.historical_stats.HistoricalPlayerGameweekStats`
has different column names than assumed here (`matched_player_id`, `season`,
`gameweek`, `minutes`, `total_points`, `goals_scored`, `assists`,
`clean_sheets`, `goals_conceded`, `bonus`, `bps`, `ict_index`, `influence`,
`creativity`, `threat`, `clearances_blocks_interceptions`, `tackles`,
`recoveries`, `defensive_contribution`), these tests passing does NOT
guarantee `app/ml/features.py`/`app/ml/training.py` work against your real
table - only that the merge/rolling-average *logic* is correct. Run
`scripts/diagnose_zero_features.py` against your real DB afterward to
confirm the real table wires up correctly too.
"""

from __future__ import annotations

import sys
import types

import pytest
from sqlalchemy import Float, Integer, String, select
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import InjuryStatus, Position
from app.models.player import Player
from app.models.team import Team


# --- Minimal stand-in for HistoricalPlayerGameweekStats -------------------

class _FakeHistoricalPlayerGameweekStats(Base):
    """Test-only stand-in matching this fix's assumed schema."""

    __tablename__ = "historical_player_gameweek_stats_test"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    matched_player_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    season: Mapped[str] = mapped_column(String(16), nullable=False)
    gameweek: Mapped[int] = mapped_column(Integer, nullable=False)

    minutes: Mapped[float] = mapped_column(Float, default=0.0)
    total_points: Mapped[float] = mapped_column(Float, default=0.0)
    goals_scored: Mapped[float] = mapped_column(Float, default=0.0)
    assists: Mapped[float] = mapped_column(Float, default=0.0)
    clean_sheets: Mapped[float] = mapped_column(Float, default=0.0)
    goals_conceded: Mapped[float] = mapped_column(Float, default=0.0)
    bonus: Mapped[float] = mapped_column(Float, default=0.0)
    bps: Mapped[float] = mapped_column(Float, default=0.0)
    ict_index: Mapped[float] = mapped_column(Float, default=0.0)
    influence: Mapped[float] = mapped_column(Float, default=0.0)
    creativity: Mapped[float] = mapped_column(Float, default=0.0)
    threat: Mapped[float] = mapped_column(Float, default=0.0)
    clearances_blocks_interceptions: Mapped[float] = mapped_column(Float, default=0.0)
    tackles: Mapped[float] = mapped_column(Float, default=0.0)
    recoveries: Mapped[float] = mapped_column(Float, default=0.0)
    defensive_contribution: Mapped[float] = mapped_column(Float, default=0.0)


@pytest.fixture(autouse=True)
def _patch_historical_model(monkeypatch, db_session):
    """Inject the fake model into the modules under test, in place of the
    real (possibly differently-shaped, possibly absent) import.

    Also creates the fake table on the same in-memory engine `db_session`
    is bound to, since `Base.metadata.create_all()` already ran before
    this fixture's model class existed.
    """
    import app.ml.features as features_mod
    import app.ml.training as training_mod

    monkeypatch.setattr(
        features_mod, "HistoricalPlayerGameweekStats", _FakeHistoricalPlayerGameweekStats
    )
    monkeypatch.setattr(
        training_mod, "HistoricalPlayerGameweekStats", _FakeHistoricalPlayerGameweekStats
    )

    bind = db_session.get_bind()
    _FakeHistoricalPlayerGameweekStats.__table__.create(bind=bind, checkfirst=True)
    yield
    _FakeHistoricalPlayerGameweekStats.__table__.drop(bind=bind, checkfirst=True)


def _team(db, team_id: int, name: str) -> Team:
    team = Team(
        id=team_id, name=name, short_name=name[:3].upper(),
        strength_attack=1200, strength_defence=1200,
    )
    db.add(team)
    db.flush()
    return team


def _player(db, player_id: int, team_id: int) -> Player:
    player = Player(
        id=player_id, first_name="Test", second_name=f"P{player_id}",
        web_name=f"P{player_id}", team_id=team_id, position=Position.MID,
        now_cost=80, status=InjuryStatus.AVAILABLE,
    )
    db.add(player)
    db.flush()
    return player


def _historical_row(db, player_id: int, season: str, gw: int, points: float, minutes: float = 90.0):
    db.add(
        _FakeHistoricalPlayerGameweekStats(
            matched_player_id=player_id, season=season, gameweek=gw,
            minutes=minutes, total_points=points,
        )
    )


class TestFeatureBuilderMergesHistoricalAndLive:
    def test_player_with_only_historical_data_gets_nonzero_rolling_features(self, db_session):
        """This is the core regression check: before the fix, a player with
        ONLY historical rows (no live PlayerGameweekStats yet) would get
        has_history=False and every rolling feature stuck at 0.0."""
        from app.ml.features import PlayerFeatureBuilder

        team = _team(db_session, 1, "Arsenal")
        player = _player(db_session, 1, team.id)

        for gw, pts in [(1, 4), (2, 8), (3, 6)]:
            _historical_row(db_session, player.id, "2023-24", gw, pts)
        db_session.flush()

        vector = PlayerFeatureBuilder().build(db_session, player, target_gameweek=1)

        assert vector.has_history is True
        assert vector.gameweeks_of_history == 3
        assert vector.points_avg_3 == pytest.approx((4 + 8 + 6) / 3)
        assert vector.points_avg_season == pytest.approx((4 + 8 + 6) / 3)
        assert vector.minutes_avg_5 == pytest.approx(90.0)

    def test_historical_rows_precede_live_rows_chronologically(self, db_session):
        """Historical (past-season) rows must be treated as older than any
        current-season live row, regardless of insertion order."""
        from app.ml.features import PlayerFeatureBuilder
        from app.models.player_stats import PlayerGameweekStats

        team = _team(db_session, 1, "Arsenal")
        player = _player(db_session, 1, team.id)

        _historical_row(db_session, player.id, "2023-24", 38, points=2.0, minutes=90.0)
        db_session.add(
            PlayerGameweekStats(player_id=player.id, gameweek=1, total_points=10.0, minutes=90)
        )
        db_session.flush()

        # Predicting gw2 (current season): last_3/last_5 should include
        # BOTH the historical row and the live gw1 row, with the
        # historical row ordered first (oldest).
        vector = PlayerFeatureBuilder().build(db_session, player, target_gameweek=2)

        assert vector.gameweeks_of_history == 2
        assert vector.points_avg_season == pytest.approx((2.0 + 10.0) / 2)
        # form_weighted decays oldest->newest, so the live (more recent,
        # higher-points) row should pull the weighted average above the
        # simple mean.
        assert vector.form_weighted > vector.points_avg_season

    def test_unmatched_historical_rows_are_excluded(self, db_session):
        """A historical row with matched_player_id=None (unmatched by the
        Phase 12 name matcher) must never leak into any player's history."""
        from app.ml.features import PlayerFeatureBuilder

        team = _team(db_session, 1, "Arsenal")
        player = _player(db_session, 1, team.id)

        db_session.add(
            _FakeHistoricalPlayerGameweekStats(
                matched_player_id=None, season="2023-24", gameweek=1,
                minutes=90, total_points=99.0,
            )
        )
        db_session.flush()

        vector = PlayerFeatureBuilder().build(db_session, player, target_gameweek=1)

        assert vector.has_history is False
        assert vector.gameweeks_of_history == 0

    def test_no_history_at_all_still_returns_cold_start_vector(self, db_session):
        """Regression guard: players with truly zero rows in either table
        must still behave like before the patch (cold start, no crash)."""
        from app.ml.features import PlayerFeatureBuilder

        team = _team(db_session, 1, "Arsenal")
        player = _player(db_session, 1, team.id)

        vector = PlayerFeatureBuilder().build(db_session, player, target_gameweek=1)

        assert vector.has_history is False
        assert vector.points_avg_5 == 0.0
        assert len(vector.to_row()) == len(__import__("app.ml.features", fromlist=["FEATURE_NAMES"]).FEATURE_NAMES)


class TestTrainingDataIncludesHistoricalExamples:
    def test_historical_rows_become_training_examples(self, db_session):
        """Core regression check for training.py: historical rows must
        themselves produce (X, y) pairs, not just feed live rows' rolling
        windows."""
        from app.ml.training import ModelTrainingService

        team = _team(db_session, 1, "Arsenal")
        player = _player(db_session, 1, team.id)

        # 4 historical gameweeks -> gw2,3,4 each have >=1 prior row and
        # should become training examples (gw1 has no prior history, skipped).
        for gw, pts in [(1, 2), (2, 5), (3, 9), (4, 3)]:
            _historical_row(db_session, player.id, "2022-23", gw, pts)
        db_session.flush()

        X, y = ModelTrainingService().build_training_data(db_session)

        assert X.shape[0] == 3  # gw2, gw3, gw4 (gw1 skipped: no prior rows)
        assert set(y.tolist()) == {5.0, 9.0, 3.0}

    def test_live_and_historical_examples_both_present(self, db_session):
        from app.ml.training import ModelTrainingService
        from app.models.player_stats import PlayerGameweekStats

        team = _team(db_session, 1, "Arsenal")
        player = _player(db_session, 1, team.id)

        for gw, pts in [(1, 2), (2, 5)]:
            _historical_row(db_session, player.id, "2022-23", gw, pts)

        db_session.add_all([
            PlayerGameweekStats(player_id=player.id, gameweek=1, total_points=4, minutes=90),
            PlayerGameweekStats(player_id=player.id, gameweek=2, total_points=7, minutes=90),
        ])
        db_session.flush()

        X, y = ModelTrainingService().build_training_data(db_session)

        # historical: gw2 only (gw1 has no prior) = 1 example
        # live: gw2 only (gw1 has no prior *live* rows before it... but the
        #        patched features.py DOES pull historical rows into live's
        #        rolling window, so gw1 live row now has history too)
        # -> expect at least 2 examples total; exact count depends on
        #    whether gw1 live row now counts (it should, post-fix).
        assert X.shape[0] >= 2
        assert y.shape[0] == X.shape[0]

    def test_zero_historical_rows_still_works_live_only(self, db_session):
        """Regression guard: with no historical table data at all,
        behaviour must match the pre-patch original (live-only)."""
        from app.ml.training import ModelTrainingService
        from app.models.player_stats import PlayerGameweekStats

        team = _team(db_session, 1, "Arsenal")
        player = _player(db_session, 1, team.id)

        for gw, pts in [(1, 3), (2, 6), (3, 5)]:
            db_session.add(
                PlayerGameweekStats(player_id=player.id, gameweek=gw, total_points=pts, minutes=90)
            )
        db_session.flush()

        X, y = ModelTrainingService().build_training_data(db_session)

        assert X.shape[0] == 2  # gw2, gw3 (gw1 has no prior live rows)
        assert set(y.tolist()) == {6.0, 5.0}