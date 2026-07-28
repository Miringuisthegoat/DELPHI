"""Unit tests for Phase 2: database schema and ORM models.

These tests exercise the schema itself (columns, relationships, unique
constraints, computed properties) rather than any business logic, since
business logic is introduced in later phases.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    Fixture,
    InjuryStatus,
    Player,
    PlayerGameweekStats,
    Position,
    Prediction,
    SquadPlayer,
    SquadState,
    Team,
    TransferDecision,
    TransferHistory,
)


def _make_team(team_id: int, name: str) -> Team:
    return Team(
        id=team_id,
        name=name,
        short_name=name[:3].upper(),
        strength_attack=1200,
        strength_defence=1100,
    )


def _make_player(player_id: int, team_id: int, position: Position = Position.MID) -> Player:
    return Player(
        id=player_id,
        first_name="Test",
        second_name=f"Player{player_id}",
        web_name=f"Player{player_id}",
        team_id=team_id,
        position=position,
        now_cost=85,  # £8.5m
        ownership_percent=12.5,
        status=InjuryStatus.AVAILABLE,
    )


class TestTeamAndPlayer:
    def test_create_team_and_player(self, db_session: Session) -> None:
        team = _make_team(1, "Liverpool")
        db_session.add(team)
        db_session.commit()

        player = _make_player(101, team_id=team.id, position=Position.FWD)
        db_session.add(player)
        db_session.commit()

        fetched = db_session.get(Player, 101)
        assert fetched is not None
        assert fetched.web_name == "Player101"
        assert fetched.position == Position.FWD
        assert fetched.price_millions == pytest.approx(8.5)
        assert fetched.full_name == "Test Player101"
        assert fetched.team.name == "Liverpool"

    def test_team_player_relationship_is_bidirectional(self, db_session: Session) -> None:
        team = _make_team(2, "Arsenal")
        db_session.add(team)
        db_session.commit()

        p1 = _make_player(201, team.id)
        p2 = _make_player(202, team.id)
        db_session.add_all([p1, p2])
        db_session.commit()

        db_session.refresh(team)
        assert {p.id for p in team.players} == {201, 202}


class TestFixture:
    def test_fixture_difficulty_is_directional(self, db_session: Session) -> None:
        home = _make_team(3, "Everton")
        away = _make_team(4, "Chelsea")
        db_session.add_all([home, away])
        db_session.commit()

        fixture = Fixture(
            id=1,
            gameweek=8,
            home_team_id=home.id,
            away_team_id=away.id,
            home_difficulty=4,
            away_difficulty=2,
        )
        db_session.add(fixture)
        db_session.commit()

        assert fixture.difficulty_for(home.id) == 4
        assert fixture.difficulty_for(away.id) == 2

        with pytest.raises(ValueError):
            fixture.difficulty_for(999)


class TestPlayerGameweekStats:
    def test_unique_constraint_on_player_and_gameweek(self, db_session: Session) -> None:
        team = _make_team(5, "Spurs")
        db_session.add(team)
        db_session.commit()

        player = _make_player(301, team.id)
        db_session.add(player)
        db_session.commit()

        stats_a = PlayerGameweekStats(
            player_id=player.id, gameweek=1, minutes=90, total_points=6
        )
        db_session.add(stats_a)
        db_session.commit()

        stats_b = PlayerGameweekStats(
            player_id=player.id, gameweek=1, minutes=90, total_points=2
        )
        db_session.add(stats_b)
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()


class TestPrediction:
    def test_record_actual_computes_error(self, db_session: Session) -> None:
        team = _make_team(6, "Newcastle")
        db_session.add(team)
        db_session.commit()

        player = _make_player(401, team.id)
        db_session.add(player)
        db_session.commit()

        prediction = Prediction(
            player_id=player.id,
            gameweek=10,
            horizon=1,
            predicted_points=6.5,
            confidence=0.8,
        )
        db_session.add(prediction)
        db_session.commit()

        prediction.record_actual(9.0)
        db_session.commit()

        assert prediction.actual_points == 9.0
        assert prediction.prediction_error == pytest.approx(2.5)


class TestSquadStateAndTransferHistory:
    def test_squad_state_with_players(self, db_session: Session) -> None:
        team = _make_team(7, "Man City")
        db_session.add(team)
        db_session.commit()

        gk = _make_player(501, team.id, Position.GKP)
        fwd = _make_player(502, team.id, Position.FWD)
        db_session.add_all([gk, fwd])
        db_session.commit()

        squad_state = SquadState(
            gameweek=1,
            bank_balance=5,
            squad_value=1000,
            free_transfers=1,
            chips_available=["wildcard", "free_hit", "bench_boost", "triple_captain"],
        )
        db_session.add(squad_state)
        db_session.commit()

        squad_state.players.append(
            SquadPlayer(
                player_id=gk.id,
                purchase_price=45,
                selling_price=45,
                is_starting=True,
            )
        )
        squad_state.players.append(
            SquadPlayer(
                player_id=fwd.id,
                purchase_price=110,
                selling_price=110,
                is_starting=True,
                is_captain=True,
            )
        )
        db_session.commit()

        db_session.refresh(squad_state)
        assert len(squad_state.players) == 2
        assert any(sp.is_captain for sp in squad_state.players)

    def test_squad_state_gameweek_is_unique(self, db_session: Session) -> None:
        db_session.add(SquadState(gameweek=1, bank_balance=0, squad_value=1000))
        db_session.commit()

        db_session.add(SquadState(gameweek=1, bank_balance=5, squad_value=1005))
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_transfer_history_tracks_expected_and_actual_gain(
        self, db_session: Session
    ) -> None:
        team = _make_team(8, "Brighton")
        db_session.add(team)
        db_session.commit()

        player_out = _make_player(601, team.id)
        player_in = _make_player(602, team.id)
        db_session.add_all([player_out, player_in])
        db_session.commit()

        transfer = TransferHistory(
            gameweek=12,
            player_out_id=player_out.id,
            player_in_id=player_in.id,
            decision_type=TransferDecision.ONE_TRANSFER,
            expected_gain=5.8,
            reasoning="Better fixtures and higher expected minutes.",
        )
        db_session.add(transfer)
        db_session.commit()

        fetched = db_session.get(TransferHistory, transfer.id)
        assert fetched is not None
        assert fetched.expected_gain == pytest.approx(5.8)
        assert fetched.actual_gain is None
        assert fetched.was_hit is False
