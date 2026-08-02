"""
Phase 10: end-to-end integration tests for `WeeklyPipelineService`.

Unlike every other test file in this project (which exercises one
service in isolation), these tests seed a full mini "season" - teams,
players, fixtures, a squad, and a prior gameweek's played-out stats -
and run the *real* prediction engine, *real* OR-Tools optimizer, and
*real* report builder together, exactly as `scripts.run_weekly_pipeline`
would. This is the "does the whole chain actually work together" check
the project's Phase 10 plan called for, on top of every phase's existing
unit tests.

No FPL API calls and no real project database are used - just the same
throwaway in-memory SQLite `db_session` fixture every other test uses.
"""

from __future__ import annotations

from app.models.enums import InjuryStatus, Position
from app.models.fixture import Fixture
from app.models.player import Player
from app.models.player_stats import PlayerGameweekStats
from app.models.squad import SquadPlayer, SquadState
from app.models.team import Team
from app.services.pipeline import WeeklyPipelineService

_GAMEWEEK = 6


def _team(db, team_id: int, name: str) -> Team:
    team = Team(id=team_id, name=name, short_name=name[:3].upper())
    db.add(team)
    db.flush()
    return team


def _player(db, player_id: int, team_id: int, position: Position, now_cost: int = 60) -> Player:
    player = Player(
        id=player_id,
        first_name="Test",
        second_name=f"P{player_id}",
        web_name=f"P{player_id}",
        team_id=team_id,
        position=position,
        now_cost=now_cost,
        status=InjuryStatus.AVAILABLE,
        is_active=True,
    )
    db.add(player)
    db.flush()
    return player


def _seed_full_world(db) -> None:
    """15-man squad spread over 5 clubs, plus fixtures for the target
    gameweek and a played-out previous gameweek (so evaluation has
    something real to backfill)."""
    positions = (
        [Position.GKP] * 2 + [Position.DEF] * 5 + [Position.MID] * 5 + [Position.FWD] * 3
    )
    for team_id in range(1, 6):
        _team(db, team_id, f"Team{team_id}")
    opponent = _team(db, 6, "Rivals")

    state = SquadState(
        gameweek=_GAMEWEEK, bank_balance=20, squad_value=750, free_transfers=1
    )
    db.add(state)
    db.flush()

    for i, position in enumerate(positions, start=1):
        team_id = ((i - 1) // 3) + 1
        _player(db, i, team_id, position, now_cost=55)
        is_starting = i <= 11
        state.players.append(
            SquadPlayer(
                player_id=i,
                purchase_price=55,
                selling_price=55,
                is_starting=is_starting,
                bench_position=None if is_starting else i - 11,
                is_captain=(i == 1),
                is_vice_captain=(i == 2),
            )
        )
        # A previous, already-played gameweek's stats - lets the pipeline's
        # evaluate_previous step actually backfill something real, and
        # gives the heuristic predictor a little history to blend in.
        db.add(
            PlayerGameweekStats(
                player_id=i,
                gameweek=_GAMEWEEK - 1,
                minutes=90,
                total_points=4,
            )
        )
    db.flush()

    db.add(
        Fixture(
            id=1,
            gameweek=_GAMEWEEK,
            home_team_id=1,
            away_team_id=opponent.id,
            home_difficulty=2,
            away_difficulty=4,
        )
    )
    db.flush()


class TestFullPipeline:
    def test_pipeline_runs_predict_evaluate_and_report_together(self, db_session, tmp_path, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(settings, "ml_model_dir", tmp_path)
        _seed_full_world(db_session)

        result = WeeklyPipelineService().run(db_session, gameweek=_GAMEWEEK, horizons=(1,))

        # Predictions were generated for every active player.
        assert result.generation.predictions_created == 15
        assert result.generation.model_used == "heuristic"

        # Evaluation backfilled the previous gameweek's horizon-1 predictions.
        # (They only exist if gw-1 predictions were ever generated - run
        # once more for gw-1 to give evaluate_previous something to find.)
        prior = WeeklyPipelineService().run(
            db_session, gameweek=_GAMEWEEK - 1, horizons=(1,), evaluate_previous=False
        )
        assert prior.generation.predictions_created == 15

        second_result = WeeklyPipelineService().run(
            db_session, gameweek=_GAMEWEEK, horizons=(1,), evaluate_previous=True
        )
        assert second_result.evaluation is not None
        assert second_result.evaluation.predictions_evaluated == 15

        # The report reflects real squad/optimizer/prediction data, not stubs.
        markdown = second_result.report.to_markdown()
        assert "DELPHI Weekly Report" in markdown
        headings = [s.heading for s in second_result.report.sections]
        assert "Squad Snapshot" in headings
        assert "Projected Points" in headings
        assert "Transfer Suggestion" in headings

        assert second_result.duration_seconds >= 0.0

    def test_pipeline_is_safe_to_rerun_and_does_not_duplicate_predictions(
        self, db_session, tmp_path, monkeypatch
    ):
        from app.core.config import settings
        from app.models.prediction import Prediction

        monkeypatch.setattr(settings, "ml_model_dir", tmp_path)
        _seed_full_world(db_session)

        service = WeeklyPipelineService()
        service.run(db_session, gameweek=_GAMEWEEK, horizons=(1,), evaluate_previous=False)
        service.run(db_session, gameweek=_GAMEWEEK, horizons=(1,), evaluate_previous=False)

        count = (
            db_session.query(Prediction)
            .filter_by(gameweek=_GAMEWEEK, horizon=1)
            .count()
        )
        assert count == 15  # upserted, not duplicated - matches Phase 5's guarantee

    def test_pipeline_handles_missing_squad_gracefully(self, db_session, tmp_path, monkeypatch):
        """No squad synced at all for this gameweek: predictions still
        generate (there's a global player pool), but the report should
        explain the missing squad rather than raising."""
        from app.core.config import settings

        monkeypatch.setattr(settings, "ml_model_dir", tmp_path)

        team = _team(db_session, 1, "Solo")
        _player(db_session, 1, team.id, Position.FWD)

        result = WeeklyPipelineService().run(
            db_session, gameweek=1, horizons=(1,), evaluate_previous=False
        )

        assert result.generation.predictions_created == 1
        headings = [s.heading for s in result.report.sections]
        assert "Squad Not Synced" in headings
