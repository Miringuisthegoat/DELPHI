"""Tests for `app.services.dashboard.DashboardService`."""

from __future__ import annotations

from enums import InjuryStatus, Position
from app.models.fixture import Fixture
from app.models.player import Player
from app.models.prediction import Prediction
from app.models.squad import SquadPlayer, SquadState
from app.models.team import Team
from app.services.dashboard import DashboardService

_GAMEWEEK = 5


def _team(db, team_id: int, name: str) -> Team:
    team = Team(id=team_id, name=name, short_name=name[:3].upper())
    db.add(team)
    db.flush()
    return team


def _player(
    db, player_id: int, team_id: int, position: Position, now_cost: int = 80, **kwargs
) -> Player:
    player = Player(
        id=player_id,
        first_name="Test",
        second_name=f"P{player_id}",
        web_name=f"P{player_id}",
        team_id=team_id,
        position=position,
        now_cost=now_cost,
        status=kwargs.pop("status", InjuryStatus.AVAILABLE),
        is_active=True,
        **kwargs,
    )
    db.add(player)
    db.flush()
    return player


def _prediction(db, player_id: int, points: float, gameweek: int = _GAMEWEEK) -> None:
    db.add(
        Prediction(
            player_id=player_id,
            gameweek=gameweek,
            horizon=1,
            predicted_points=points,
            confidence=0.7,
        )
    )


def _basic_squad(db) -> SquadState:
    """2 GKP / 5 DEF / 5 MID / 3 FWD, spread across 5 clubs (<=3/club)."""
    positions = (
        [Position.GKP] * 2 + [Position.DEF] * 5 + [Position.MID] * 5 + [Position.FWD] * 3
    )
    for team_id in range(1, 6):
        _team(db, team_id, f"Team{team_id}")

    state = SquadState(
        gameweek=_GAMEWEEK, bank_balance=15, squad_value=750, free_transfers=1
    )
    db.add(state)
    db.flush()

    for i, position in enumerate(positions, start=1):
        team_id = ((i - 1) // 3) + 1
        _player(db, i, team_id, position, now_cost=50)
        is_starting = i <= 11
        state.players.append(
            SquadPlayer(
                player_id=i,
                purchase_price=50,
                selling_price=50,
                is_starting=is_starting,
                bench_position=None if is_starting else i - 11,
                is_captain=(i == 1),
                is_vice_captain=(i == 2),
            )
        )
    db.flush()
    return state


class TestNoSquad:
    def test_no_squad_state_returns_empty_view(self, db_session):
        view = DashboardService().build_view(db_session, gameweek=1)

        assert view.has_squad is False
        assert view.squad_rows == []
        assert view.optimization_error is not None


class TestSquadPresentNoPredictions:
    def test_squad_without_predictions_has_projected_points_zero(self, db_session):
        _basic_squad(db_session)

        view = DashboardService().build_view(db_session, gameweek=_GAMEWEEK)

        assert view.has_squad is True
        assert view.has_predictions is False
        assert len(view.squad_rows) == 15
        assert view.projected_points == 0.0
        assert view.captain is None
        assert view.optimization is None
        assert view.optimization_error is not None


class TestSquadWithPredictions:
    def test_captain_is_highest_predicted_starter(self, db_session):
        _basic_squad(db_session)
        for i in range(1, 16):
            _prediction(db_session, i, points=4.0)
        # Player 3 (a starter) has the highest projection.
        _prediction(db_session, 3, points=11.0)
        db_session.flush()

        view = DashboardService().build_view(db_session, gameweek=_GAMEWEEK)

        assert view.has_predictions is True
        assert view.captain is not None
        assert view.captain.player_id == 3
        assert view.vice_captain is not None
        assert view.vice_captain.player_id != 3

    def test_projected_points_doubles_captain_contribution(self, db_session):
        _basic_squad(db_session)
        for i in range(1, 16):
            _prediction(db_session, i, points=4.0)
        db_session.flush()

        view = DashboardService().build_view(db_session, gameweek=_GAMEWEEK)

        # 11 starters * 4.0 + one extra 4.0 for the captain's double = 48.0
        assert view.projected_points == 48.0

    def test_bench_players_do_not_count_toward_projected_points(self, db_session):
        _basic_squad(db_session)
        for i in range(1, 16):
            # Give bench players (12-15) huge scores; they must not count.
            points = 100.0 if i > 11 else 4.0
            _prediction(db_session, i, points=points)
        db_session.flush()

        view = DashboardService().build_view(db_session, gameweek=_GAMEWEEK)

        assert view.projected_points == 48.0


class TestInjuryAlerts:
    def test_unavailable_player_appears_in_alerts(self, db_session):
        _basic_squad(db_session)
        injured = db_session.get(Player, 5)
        injured.status = InjuryStatus.INJURED
        injured.news = "Hamstring injury, expected back in 4 weeks."
        db_session.flush()

        view = DashboardService().build_view(db_session, gameweek=_GAMEWEEK)

        assert any(a.player_id == 5 for a in view.injury_alerts)


class TestFixtureTicker:
    def test_ticker_includes_upcoming_fixtures_for_owned_teams(self, db_session):
        _basic_squad(db_session)
        # Team 1 has a player owned (player id 1-3); add a fixture for them.
        opponent = _team(db_session, 6, "Rivals")
        db_session.add(
            Fixture(
                id=1,
                gameweek=_GAMEWEEK,
                home_team_id=1,
                away_team_id=opponent.id,
                home_difficulty=2,
                away_difficulty=4,
            )
        )
        db_session.flush()

        view = DashboardService().build_view(db_session, gameweek=_GAMEWEEK)

        assert any(e.team_short_name == "TEA" for e in view.fixture_ticker)


class TestGameweekFallback:
    def test_uses_most_recent_squad_state_at_or_before_gameweek(self, db_session):
        _basic_squad(db_session)

        view = DashboardService().build_view(db_session, gameweek=_GAMEWEEK + 2)

        assert view.has_squad is True
        assert view.squad_gameweek == _GAMEWEEK
