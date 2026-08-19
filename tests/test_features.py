"""Tests for `app.ml.features.PlayerFeatureBuilder`."""

from __future__ import annotations

from app.ml.features import FEATURE_NAMES, PlayerFeatureBuilder
from enums import InjuryStatus, Position
from app.models.fixture import Fixture
from app.models.player import Player
from app.models.player_stats import PlayerGameweekStats
from app.models.team import Team


def _make_team(db, team_id: int, name: str, attack: int = 1200, defence: int = 1200) -> Team:
    team = Team(
        id=team_id,
        name=name,
        short_name=name[:3].upper(),
        strength_attack=attack,
        strength_defence=defence,
    )
    db.add(team)
    db.flush()
    return team


def _make_player(db, player_id: int, team_id: int, **kwargs) -> Player:
    player = Player(
        id=player_id,
        first_name="Test",
        second_name=f"Player{player_id}",
        web_name=f"Player{player_id}",
        team_id=team_id,
        position=kwargs.pop("position", Position.MID),
        now_cost=kwargs.pop("now_cost", 80),
        status=kwargs.pop("status", InjuryStatus.AVAILABLE),
        **kwargs,
    )
    db.add(player)
    db.flush()
    return player


def test_cold_start_has_no_history(db_session):
    team = _make_team(db_session, 1, "Arsenal")
    player = _make_player(db_session, 1, team.id)

    vector = PlayerFeatureBuilder().build(db_session, player, target_gameweek=1)

    assert vector.has_history is False
    assert vector.gameweeks_of_history == 0
    assert vector.points_avg_5 == 0.0
    assert len(vector.to_row()) == len(FEATURE_NAMES)


def test_rolling_averages_use_only_prior_gameweeks(db_session):
    team = _make_team(db_session, 1, "Arsenal")
    player = _make_player(db_session, 1, team.id)

    for gw, pts in [(1, 2), (2, 8), (3, 4)]:
        db_session.add(
            PlayerGameweekStats(player_id=player.id, gameweek=gw, total_points=pts, minutes=90)
        )
    db_session.flush()

    # Predicting gw 4: should see all three prior gameweeks.
    vector = PlayerFeatureBuilder().build(db_session, player, target_gameweek=4)
    assert vector.gameweeks_of_history == 3
    assert vector.points_avg_3 == (2 + 8 + 4) / 3

    # Predicting gw 3: must NOT see gw 3's own (not-yet-happened) result.
    vector_gw3 = PlayerFeatureBuilder().build(db_session, player, target_gameweek=3)
    assert vector_gw3.gameweeks_of_history == 2
    assert vector_gw3.points_avg_season == (2 + 8) / 2


def test_fixture_context_applies_difficulty_and_home_flag(db_session):
    home_team = _make_team(db_session, 1, "Arsenal", attack=1300, defence=1250)
    away_team = _make_team(db_session, 2, "Fulham", attack=1000, defence=1000)
    player = _make_player(db_session, 1, home_team.id)

    db_session.add(
        Fixture(
            id=1,
            gameweek=5,
            home_team_id=home_team.id,
            away_team_id=away_team.id,
            home_difficulty=2,
            away_difficulty=4,
        )
    )
    db_session.flush()

    vector = PlayerFeatureBuilder().build(db_session, player, target_gameweek=5)

    assert vector.fixture_difficulty == 2.0
    assert vector.is_home == 1.0
    assert vector.num_fixtures_this_gw == 1.0
    assert vector.opponent_strength_attack == 1000.0


def test_blank_gameweek_has_zero_fixtures(db_session):
    team = _make_team(db_session, 1, "Arsenal")
    player = _make_player(db_session, 1, team.id)

    vector = PlayerFeatureBuilder().build(db_session, player, target_gameweek=9)

    assert vector.num_fixtures_this_gw == 0.0


def test_double_gameweek_counts_both_fixtures(db_session):
    team = _make_team(db_session, 1, "Arsenal")
    opponent_a = _make_team(db_session, 2, "Fulham")
    opponent_b = _make_team(db_session, 3, "Brentford")
    player = _make_player(db_session, 1, team.id)

    db_session.add_all(
        [
            Fixture(
                id=1, gameweek=5, home_team_id=team.id, away_team_id=opponent_a.id,
                home_difficulty=2, away_difficulty=3,
            ),
            Fixture(
                id=2, gameweek=5, home_team_id=opponent_b.id, away_team_id=team.id,
                home_difficulty=3, away_difficulty=3,
            ),
        ]
    )
    db_session.flush()

    vector = PlayerFeatureBuilder().build(db_session, player, target_gameweek=5)
    assert vector.num_fixtures_this_gw == 2.0
