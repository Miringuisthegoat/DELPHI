"""
Unit tests for Phase 4: `app.services.ingestion`.

These exercise the mapping rules and the upsert behaviour of
`DataIngestionService` against the same throwaway in-memory SQLite
database used by the Phase 2 model tests (see `tests/conftest.py`) -
never the real FPL API or a real file-backed database.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy.orm import Session

from app.models import Fixture, InjuryStatus, Player, PlayerGameweekStats, Position, Team
from app.schemas.fpl_bootstrap import (
    FPLBootstrapStatic,
    FPLElementType,
    FPLEvent,
    FPLGameSettings,
    FPLPlayer,
    FPLTeam,
)
from app.schemas.fpl_element_summary import FPLElementHistory, FPLElementSummary
from app.schemas.fpl_fixtures import FPLFixture
from app.schemas.fpl_live import FPLEventLive, FPLLiveElement, FPLLiveStats
from app.services.ingestion import DataIngestionService


def _team(team_id: int, name: str) -> FPLTeam:
    return FPLTeam(
        id=team_id,
        name=name,
        short_name=name[:3].upper(),
        strength=4,
        strength_overall_home=1250,
        strength_overall_away=1300,
        strength_attack_home=1200,
        strength_attack_away=1250,
        strength_defence_home=1300,
        strength_defence_away=1350,
    )


def _element_type(type_id: int, short_name: str) -> FPLElementType:
    return FPLElementType(
        id=type_id,
        singular_name=short_name,
        singular_name_short=short_name,
        squad_select=5,
        squad_min_play=1,
        squad_max_play=5,
    )


def _player(
    player_id: int,
    team_id: int,
    element_type: int = 3,
    status: str = "a",
    now_cost: int = 85,
) -> FPLPlayer:
    return FPLPlayer(
        id=player_id,
        first_name="Test",
        second_name=f"Player{player_id}",
        web_name=f"Player{player_id}",
        team=team_id,
        element_type=element_type,
        now_cost=now_cost,
        selected_by_percent="12.5",
        form="4.2",
        points_per_game="5.0",
        status=status,
        transfers_in_event=1000,
        transfers_out_event=200,
    )


def _bootstrap(teams: list[FPLTeam], players: list[FPLPlayer]) -> FPLBootstrapStatic:
    return FPLBootstrapStatic(
        events=[
            FPLEvent(
                id=1,
                name="Gameweek 1",
                deadline_time=datetime(2026, 8, 15, tzinfo=timezone.utc),
                finished=True,
                is_previous=True,
                is_current=False,
                is_next=False,
            )
        ],
        teams=teams,
        element_types=[
            _element_type(1, "GKP"),
            _element_type(2, "DEF"),
            _element_type(3, "MID"),
            _element_type(4, "FWD"),
        ],
        elements=players,
        game_settings=FPLGameSettings(),
    )


def _fixture(
    fixture_id: int, event: int, team_h: int, team_a: int
) -> FPLFixture:
    return FPLFixture(
        id=fixture_id,
        event=event,
        kickoff_time=datetime(2026, 8, 16, tzinfo=timezone.utc),
        finished=False,
        team_h=team_h,
        team_a=team_a,
        team_h_difficulty=2,
        team_a_difficulty=4,
    )


class TestSyncTeams:
    def test_creates_new_teams(self, db_session: Session) -> None:
        service = DataIngestionService()
        result = service.sync_teams(db_session, [_team(1, "Arsenal"), _team(2, "Chelsea")])
        db_session.commit()

        assert result.created == 2
        assert result.updated == 0
        assert db_session.get(Team, 1).name == "Arsenal"
        assert db_session.get(Team, 2).name == "Chelsea"

    def test_updates_existing_team_in_place(self, db_session: Session) -> None:
        service = DataIngestionService()
        service.sync_teams(db_session, [_team(1, "Arsenal")])
        db_session.commit()

        renamed = _team(1, "Arsenal")
        renamed.strength_attack_home = 1400
        result = service.sync_teams(db_session, [renamed])
        db_session.commit()

        assert result.created == 0
        assert result.updated == 1
        assert db_session.query(Team).count() == 1
        assert db_session.get(Team, 1).strength_attack_home == 1400


class TestSyncPlayers:
    def test_creates_player_with_correct_position(self, db_session: Session) -> None:
        service = DataIngestionService()
        service.sync_teams(db_session, [_team(1, "Arsenal")])
        bootstrap = _bootstrap(
            teams=[_team(1, "Arsenal")],
            players=[_player(101, team_id=1, element_type=4)],  # FWD
        )
        result = service.sync_players(db_session, bootstrap)
        db_session.commit()

        assert result.created == 1
        player = db_session.get(Player, 101)
        assert player.position == Position.FWD
        assert player.team_id == 1
        assert player.price_millions == 8.5

    def test_price_trend_reflects_net_transfers(self, db_session: Session) -> None:
        service = DataIngestionService()
        service.sync_teams(db_session, [_team(1, "Arsenal")])
        bootstrap = _bootstrap(
            teams=[_team(1, "Arsenal")],
            players=[_player(101, team_id=1)],
        )
        service.sync_players(db_session, bootstrap)
        db_session.commit()

        # transfers_in_event=1000, transfers_out_event=200 -> net +800
        assert db_session.get(Player, 101).price_trend == 800.0

    def test_unrecognised_status_defaults_to_available(self, db_session: Session) -> None:
        service = DataIngestionService()
        service.sync_teams(db_session, [_team(1, "Arsenal")])
        bootstrap = _bootstrap(
            teams=[_team(1, "Arsenal")],
            players=[_player(101, team_id=1, status="n")],
        )
        result = service.sync_players(db_session, bootstrap)
        db_session.commit()

        assert result.created == 1
        # 'n' (not in squad) is folded into UNAVAILABLE, not a crash.
        assert db_session.get(Player, 101).status == InjuryStatus.UNAVAILABLE

    def test_unmapped_position_is_skipped_not_raised(self, db_session: Session) -> None:
        service = DataIngestionService()
        service.sync_teams(db_session, [_team(1, "Arsenal")])
        bootstrap = _bootstrap(
            teams=[_team(1, "Arsenal")],
            players=[_player(101, team_id=1, element_type=99)],  # unknown type
        )
        result = service.sync_players(db_session, bootstrap)
        db_session.commit()

        assert result.created == 0
        assert result.failed == 1
        assert db_session.get(Player, 101) is None

    def test_updates_existing_player_price(self, db_session: Session) -> None:
        service = DataIngestionService()
        service.sync_teams(db_session, [_team(1, "Arsenal")])
        bootstrap = _bootstrap(
            teams=[_team(1, "Arsenal")], players=[_player(101, team_id=1, now_cost=85)]
        )
        service.sync_players(db_session, bootstrap)
        db_session.commit()

        bootstrap_v2 = _bootstrap(
            teams=[_team(1, "Arsenal")], players=[_player(101, team_id=1, now_cost=90)]
        )
        result = service.sync_players(db_session, bootstrap_v2)
        db_session.commit()

        assert result.updated == 1
        assert db_session.query(Player).count() == 1
        assert db_session.get(Player, 101).now_cost == 90


class TestSyncFixtures:
    def test_creates_fixture_with_asymmetric_difficulty(self, db_session: Session) -> None:
        service = DataIngestionService()
        service.sync_teams(db_session, [_team(1, "Arsenal"), _team(2, "Chelsea")])
        result = service.sync_fixtures(db_session, [_fixture(500, event=1, team_h=1, team_a=2)])
        db_session.commit()

        assert result.created == 1
        fixture = db_session.get(Fixture, 500)
        assert fixture.difficulty_for(1) == 2  # home
        assert fixture.difficulty_for(2) == 4  # away

    def test_rerunning_sync_updates_not_duplicates(self, db_session: Session) -> None:
        service = DataIngestionService()
        service.sync_teams(db_session, [_team(1, "Arsenal"), _team(2, "Chelsea")])
        service.sync_fixtures(db_session, [_fixture(500, event=1, team_h=1, team_a=2)])
        db_session.commit()

        finished_fixture = _fixture(500, event=1, team_h=1, team_a=2)
        finished_fixture.finished = True
        finished_fixture.team_h_score = 2
        finished_fixture.team_a_score = 1
        result = service.sync_fixtures(db_session, [finished_fixture])
        db_session.commit()

        assert result.created == 0
        assert result.updated == 1
        assert db_session.query(Fixture).count() == 1
        fixture = db_session.get(Fixture, 500)
        assert fixture.finished is True
        assert fixture.home_score == 2


class TestSyncPlayerHistory:
    def _seed_player(self, db_session: Session) -> None:
        service = DataIngestionService()
        service.sync_teams(db_session, [_team(1, "Arsenal")])
        bootstrap = _bootstrap(teams=[_team(1, "Arsenal")], players=[_player(101, team_id=1)])
        service.sync_players(db_session, bootstrap)
        db_session.commit()

    def test_backfills_multiple_gameweeks(self, db_session: Session) -> None:
        self._seed_player(db_session)
        service = DataIngestionService()

        summary = FPLElementSummary(
            fixtures=[],
            history=[
                FPLElementHistory(
                    element=101,
                    fixture=500,
                    opponent_team=2,
                    total_points=6,
                    was_home=True,
                    kickoff_time=datetime(2026, 8, 16, tzinfo=timezone.utc),
                    round=1,
                    minutes=90,
                    goals_scored=1,
                    value=85,
                ),
                FPLElementHistory(
                    element=101,
                    fixture=515,
                    opponent_team=3,
                    total_points=2,
                    was_home=False,
                    kickoff_time=datetime(2026, 8, 23, tzinfo=timezone.utc),
                    round=2,
                    minutes=90,
                    value=86,
                ),
            ],
            history_past=[],
        )
        result = service.sync_player_history(db_session, player_id=101, summary=summary)
        db_session.commit()

        assert result.created == 2
        rows = (
            db_session.query(PlayerGameweekStats)
            .filter_by(player_id=101)
            .order_by(PlayerGameweekStats.gameweek)
            .all()
        )
        assert [r.gameweek for r in rows] == [1, 2]
        assert rows[0].total_points == 6
        assert rows[0].price_at_gameweek == 85

    def test_rerun_updates_same_gameweek_row(self, db_session: Session) -> None:
        self._seed_player(db_session)
        service = DataIngestionService()

        def make_summary(points: int) -> FPLElementSummary:
            return FPLElementSummary(
                fixtures=[],
                history=[
                    FPLElementHistory(
                        element=101,
                        fixture=500,
                        opponent_team=2,
                        total_points=points,
                        was_home=True,
                        kickoff_time=datetime(2026, 8, 16, tzinfo=timezone.utc),
                        round=1,
                        minutes=90,
                        value=85,
                    )
                ],
                history_past=[],
            )

        service.sync_player_history(db_session, player_id=101, summary=make_summary(6))
        db_session.commit()
        result = service.sync_player_history(db_session, player_id=101, summary=make_summary(9))
        db_session.commit()

        assert result.created == 0
        assert result.updated == 1
        rows = db_session.query(PlayerGameweekStats).filter_by(player_id=101).all()
        assert len(rows) == 1
        assert rows[0].total_points == 9


class TestSyncGameweekLive:
    def test_uses_current_player_price_as_snapshot(self, db_session: Session) -> None:
        service = DataIngestionService()
        service.sync_teams(db_session, [_team(1, "Arsenal")])
        bootstrap = _bootstrap(
            teams=[_team(1, "Arsenal")], players=[_player(101, team_id=1, now_cost=95)]
        )
        service.sync_players(db_session, bootstrap)
        db_session.commit()

        live = FPLEventLive(
            elements=[
                FPLLiveElement(
                    id=101,
                    stats=FPLLiveStats(minutes=90, goals_scored=2, total_points=12, bonus=3),
                )
            ]
        )
        result = service.sync_gameweek_live(db_session, gameweek=5, live=live)
        db_session.commit()

        assert result.created == 1
        row = (
            db_session.query(PlayerGameweekStats)
            .filter_by(player_id=101, gameweek=5)
            .one()
        )
        assert row.total_points == 12
        assert row.price_at_gameweek == 95

    def test_skips_players_not_yet_synced(self, db_session: Session) -> None:
        service = DataIngestionService()
        live = FPLEventLive(
            elements=[FPLLiveElement(id=999, stats=FPLLiveStats(total_points=5))]
        )
        result = service.sync_gameweek_live(db_session, gameweek=1, live=live)

        assert result.created == 0
        assert result.failed == 1


class TestFullBootstrapSync:
    def test_syncs_in_dependency_order(self, db_session: Session) -> None:
        service = DataIngestionService()
        bootstrap = _bootstrap(
            teams=[_team(1, "Arsenal"), _team(2, "Chelsea")],
            players=[_player(101, team_id=1), _player(102, team_id=2)],
        )
        fixtures = [_fixture(500, event=1, team_h=1, team_a=2)]

        summary = service.sync_full_bootstrap(db_session, bootstrap, fixtures)
        db_session.commit()

        assert summary.teams.created == 2
        assert summary.players.created == 2
        assert summary.fixtures.created == 1
        assert db_session.query(Team).count() == 2
        assert db_session.query(Player).count() == 2
        assert db_session.query(Fixture).count() == 1
