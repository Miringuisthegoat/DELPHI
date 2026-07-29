"""Tests for `app.ml.engine.DelphiPredictionEngine`."""

from __future__ import annotations

from app.core.config import settings
from app.ml.engine import DelphiPredictionEngine
from app.models.enums import InjuryStatus, Position
from app.models.fixture import Fixture
from app.models.player import Player
from app.models.player_stats import PlayerGameweekStats
from app.models.prediction import Prediction
from app.models.team import Team


def _seed_basic_world(db):
    home = Team(id=1, name="Arsenal", short_name="ARS", strength_attack=1300, strength_defence=1250)
    away = Team(id=2, name="Fulham", short_name="FUL", strength_attack=1000, strength_defence=1000)
    db.add_all([home, away])
    db.flush()

    player = Player(
        id=1,
        first_name="Test",
        second_name="Striker",
        web_name="Striker",
        team_id=home.id,
        position=Position.FWD,
        now_cost=90,
        status=InjuryStatus.AVAILABLE,
        is_active=True,
    )
    db.add(player)
    db.add(
        Fixture(
            id=1,
            gameweek=5,
            home_team_id=home.id,
            away_team_id=away.id,
            home_difficulty=2,
            away_difficulty=4,
        )
    )
    db.flush()
    return player


def test_generate_falls_back_to_heuristic_when_no_model(db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "ml_model_dir", tmp_path)
    _seed_basic_world(db_session)

    engine = DelphiPredictionEngine()
    summary = engine.generate_for_gameweek(db_session, gameweek=5, horizons=(1,))

    assert summary.model_used == "heuristic"
    assert summary.predictions_created == 1
    assert summary.predictions[0].predicted_points > 0
    assert summary.predictions[0].reasoning

    stored = db_session.query(Prediction).filter_by(gameweek=5, horizon=1).one()
    assert stored.model_name == f"{settings.ml_model_name}_heuristic"


def test_regenerating_updates_rather_than_duplicates(db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "ml_model_dir", tmp_path)
    _seed_basic_world(db_session)

    engine = DelphiPredictionEngine()
    first = engine.generate_for_gameweek(db_session, gameweek=5, horizons=(1,))
    second = engine.generate_for_gameweek(db_session, gameweek=5, horizons=(1,))

    assert first.predictions_created == 1
    assert second.predictions_created == 0
    assert second.predictions_updated == 1

    count = db_session.query(Prediction).filter_by(gameweek=5, horizon=1).count()
    assert count == 1


def test_evaluate_gameweek_backfills_actuals(db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "ml_model_dir", tmp_path)
    player = _seed_basic_world(db_session)

    engine = DelphiPredictionEngine()
    engine.generate_for_gameweek(db_session, gameweek=5, horizons=(1,))

    db_session.add(
        PlayerGameweekStats(player_id=player.id, gameweek=5, total_points=12, minutes=90)
    )
    db_session.flush()

    summary = engine.evaluate_gameweek(db_session, gameweek=5)

    assert summary.predictions_evaluated == 1
    assert summary.mean_absolute_error is not None

    stored = db_session.query(Prediction).filter_by(gameweek=5, horizon=1).one()
    assert stored.actual_points == 12.0
    assert stored.prediction_error == 12.0 - stored.predicted_points


def test_horizon_greater_than_one_sums_multiple_gameweeks(db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "ml_model_dir", tmp_path)
    home = Team(id=1, name="Arsenal", short_name="ARS", strength_attack=1300, strength_defence=1250)
    away = Team(id=2, name="Fulham", short_name="FUL", strength_attack=1000, strength_defence=1000)
    db_session.add_all([home, away])
    db_session.flush()

    player = Player(
        id=1, first_name="Test", second_name="Striker", web_name="Striker",
        team_id=home.id, position=Position.FWD, now_cost=90,
        status=InjuryStatus.AVAILABLE, is_active=True,
    )
    db_session.add(player)
    db_session.add_all(
        [
            Fixture(id=1, gameweek=5, home_team_id=home.id, away_team_id=away.id, home_difficulty=2, away_difficulty=4),
            Fixture(id=2, gameweek=6, home_team_id=away.id, away_team_id=home.id, home_difficulty=3, away_difficulty=3),
            Fixture(id=3, gameweek=7, home_team_id=home.id, away_team_id=away.id, home_difficulty=2, away_difficulty=4),
        ]
    )
    db_session.flush()

    engine = DelphiPredictionEngine()
    single_gw = engine.generate_for_gameweek(db_session, gameweek=5, horizons=(1,))
    three_gw = engine.generate_for_gameweek(db_session, gameweek=5, horizons=(3,))

    single_points = single_gw.predictions[0].predicted_points
    triple_points = three_gw.predictions[0].predicted_points

    assert triple_points > single_points
    # Confidence should decay for the longer horizon.
    assert three_gw.predictions[0].confidence <= single_gw.predictions[0].confidence
