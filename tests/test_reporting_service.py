"""Tests for `app.services.reporting.WeeklyReportService` and delivery channels."""

from __future__ import annotations

from app.models.enums import InjuryStatus, Position
from app.models.player import Player
from app.models.prediction import Prediction
from app.models.squad import SquadPlayer, SquadState
from app.models.team import Team
from app.services.reporting import ConsoleDeliveryChannel, TelegramDeliveryChannel
from app.services.reporting.service import WeeklyReportService

_GAMEWEEK = 5


def _team(db, team_id: int, name: str) -> Team:
    team = Team(id=team_id, name=name, short_name=name[:3].upper())
    db.add(team)
    db.flush()
    return team


def _player(db, player_id: int, team_id: int, position: Position, now_cost: int = 80, **kwargs) -> Player:
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


def _prediction(db, player_id: int, points: float, gameweek: int = _GAMEWEEK, **kwargs) -> Prediction:
    prediction = Prediction(
        player_id=player_id,
        gameweek=gameweek,
        horizon=1,
        predicted_points=points,
        confidence=0.7,
        **kwargs,
    )
    db.add(prediction)
    return prediction


def _basic_squad(db, gameweek: int = _GAMEWEEK) -> SquadState:
    positions = [Position.GKP] * 2 + [Position.DEF] * 5 + [Position.MID] * 5 + [Position.FWD] * 3
    for team_id in range(1, 6):
        _team(db, team_id, f"Team{team_id}")

    state = SquadState(gameweek=gameweek, bank_balance=15, squad_value=750, free_transfers=1)
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
    def test_no_squad_reports_explanatory_section(self, db_session):
        report = WeeklyReportService().build_report(db_session, gameweek=1)

        assert report.gameweek == 1
        assert any(s.heading == "Squad Not Synced" for s in report.sections)
        assert "sync" in report.to_markdown().lower() or "squad" in report.to_markdown().lower()


class TestSquadWithoutPredictions:
    def test_reports_squad_snapshot_and_missing_predictions_note(self, db_session):
        _basic_squad(db_session)

        report = WeeklyReportService().build_report(db_session, gameweek=_GAMEWEEK)

        headings = [s.heading for s in report.sections]
        assert "Squad Snapshot" in headings
        assert "Predictions Not Generated Yet" in headings
        assert "Projected Points" not in headings


class TestSquadWithPredictions:
    def test_reports_projection_captaincy_and_transfer_sections(self, db_session):
        _basic_squad(db_session)
        for i in range(1, 16):
            _prediction(db_session, i, points=4.0)
        _prediction(db_session, 3, points=11.0)
        db_session.flush()

        report = WeeklyReportService().build_report(db_session, gameweek=_GAMEWEEK)

        headings = [s.heading for s in report.sections]
        assert "Projected Points" in headings
        assert "Captaincy" in headings
        assert "Transfer Suggestion" in headings

        captaincy = next(s for s in report.sections if s.heading == "Captaincy")
        assert any("P3" in line for line in captaincy.lines)

    def test_markdown_and_plain_text_render_without_error(self, db_session):
        _basic_squad(db_session)
        for i in range(1, 16):
            _prediction(db_session, i, points=4.0)
        db_session.flush()

        report = WeeklyReportService().build_report(db_session, gameweek=_GAMEWEEK)

        markdown = report.to_markdown()
        plain = report.to_plain_text()
        assert "# DELPHI Weekly Report" in markdown
        assert "DELPHI WEEKLY REPORT" in plain.upper()


class TestInjuryAlerts:
    def test_injury_section_included_when_alerts_exist(self, db_session):
        _basic_squad(db_session)
        injured = db_session.get(Player, 5)
        injured.status = InjuryStatus.INJURED
        injured.news = "Hamstring injury."
        db_session.flush()

        report = WeeklyReportService().build_report(db_session, gameweek=_GAMEWEEK)

        section = next(s for s in report.sections if s.heading == "Injury / Availability Alerts")
        assert any("P5" in line for line in section.lines)


class TestAccuracySection:
    def test_omitted_when_no_evaluated_predictions_exist(self, db_session):
        _basic_squad(db_session, gameweek=_GAMEWEEK)

        report = WeeklyReportService().build_report(db_session, gameweek=_GAMEWEEK)

        assert not any(s.heading.startswith("Prediction Accuracy") for s in report.sections)

    def test_included_once_previous_gameweek_is_evaluated(self, db_session):
        _basic_squad(db_session, gameweek=_GAMEWEEK)
        # Evaluated predictions from the *previous* gameweek.
        _prediction(
            db_session, 1, points=5.0, gameweek=_GAMEWEEK - 1,
            actual_points=7.0, prediction_error=2.0,
        )
        _prediction(
            db_session, 2, points=6.0, gameweek=_GAMEWEEK - 1,
            actual_points=6.5, prediction_error=0.5,
        )
        db_session.flush()

        report = WeeklyReportService().build_report(db_session, gameweek=_GAMEWEEK)

        section = next(
            s for s in report.sections if s.heading.startswith("Prediction Accuracy")
        )
        assert any("mean absolute error" in line for line in section.lines)


class TestDeliveryChannels:
    def test_console_channel_delivers_successfully(self, db_session):
        _basic_squad(db_session)
        report = WeeklyReportService().build_report(db_session, gameweek=_GAMEWEEK)

        result = ConsoleDeliveryChannel().send(report)

        assert result.delivered is True
        assert result.channel == "console"

    def test_telegram_channel_is_not_yet_implemented(self, db_session):
        _basic_squad(db_session)
        report = WeeklyReportService().build_report(db_session, gameweek=_GAMEWEEK)

        try:
            TelegramDeliveryChannel().send(report)
            assert False, "expected NotImplementedError"
        except NotImplementedError:
            pass
