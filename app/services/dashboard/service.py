"""
Phase 8: `DashboardService` - assembles everything the dashboard template
needs into one plain-dataclass view model, in a single call.

Design notes
------------
* **Read-only, service-layer aggregation, not a route.** The FastAPI route
  in `app/web/routes.py` stays a thin adapter (parse query params, call
  this, render the template) - every actual query and cross-referencing
  of Phase 5 (`Prediction`), Phase 6 (`TransferOptimizerService`), and
  Phase 7 (`SquadState`/`SquadPlayer`) output lives here, matching the
  project's existing `*Service` convention (stateless aside from the
  `Session` passed per call).
* **Never lets the optimizer's absence break the page.** `TransferOptimizerService.optimize()`
  raises `OptimizationError` whenever squad/prediction data isn't ready
  yet (see Phase 6/7 READMEs - this is expected during early setup, not
  a bug). The dashboard catches that and renders an explanatory empty
  state instead of a 500.
* **"At or before" gameweek lookups, matching Phase 6/7's own convention.**
  A `SquadState` snapshot stays valid until the next gameweek's picks are
  synced, so `gameweek` here is "the gameweek to plan for", not
  necessarily one with its own exact `SquadState` row.
* **Captain suggestion is derived, not stored.** DELPHI doesn't persist a
  separate "captain" concept (Phase 5's `Prediction` table is per-player/
  gameweek/horizon) - the dashboard simply picks the *starting* squad
  player with the highest horizon-1 predicted points, which is exactly
  what "who should I captain" means in FPL.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import OptimizationError
from enums import InjuryStatus, Position
from app.models.fixture import Fixture
from app.models.player import Player
from app.models.prediction import Prediction
from app.models.squad import SquadState
from app.models.team import Team
from app.optimization.models import OptimizationResult
from app.optimization.transfer_optimizer import TransferOptimizerService

_POSITION_ORDER: dict[Position, int] = {
    Position.GKP: 0,
    Position.DEF: 1,
    Position.MID: 2,
    Position.FWD: 3,
}

_FIXTURE_LOOKAHEAD = 5
"""How many upcoming gameweeks the fixture ticker shows per squad team."""


@dataclass
class SquadRow:
    """One row of the squad table."""

    player_id: int
    web_name: str
    team_short_name: str
    position: str
    price_millions: float
    is_starting: bool
    bench_position: int | None
    is_captain: bool
    is_vice_captain: bool
    status: str
    news: str | None
    predicted_points: float | None
    """Horizon-1 predicted points for the dashboard's gameweek, if generated."""


@dataclass
class InjuryAlert:
    player_id: int
    web_name: str
    status: str
    chance_of_playing_next_round: int | None
    news: str | None


@dataclass
class FixtureTickerEntry:
    team_id: int
    team_short_name: str
    gameweek: int
    opponent_short_name: str
    is_home: bool
    difficulty: int


@dataclass
class CaptainSuggestion:
    player_id: int
    web_name: str
    predicted_points: float
    reasoning: str


@dataclass
class DashboardView:
    """Everything the dashboard template needs, pre-formatted."""

    gameweek: int
    generated_at: datetime

    has_squad: bool
    squad_gameweek: int | None
    bank_millions: float
    squad_value_millions: float
    free_transfers: int
    chips_available: list[str]
    chip_played: str | None
    overall_rank: int | None
    total_points: int

    squad_rows: list[SquadRow] = field(default_factory=list)
    injury_alerts: list[InjuryAlert] = field(default_factory=list)
    fixture_ticker: list[FixtureTickerEntry] = field(default_factory=list)

    projected_points: float = 0.0
    captain: CaptainSuggestion | None = None
    vice_captain: CaptainSuggestion | None = None

    optimization: OptimizationResult | None = None
    optimization_error: str | None = None

    has_predictions: bool = False


class DashboardService:
    """Builds the Phase 8 dashboard's view model from the database."""

    def __init__(self, optimizer: TransferOptimizerService | None = None) -> None:
        self._optimizer = optimizer or TransferOptimizerService()

    def build_view(self, db: Session, gameweek: int) -> DashboardView:
        squad_state = self._load_squad_state(db, gameweek)

        if squad_state is None:
            return DashboardView(
                gameweek=gameweek,
                generated_at=datetime.now(timezone.utc),
                has_squad=False,
                squad_gameweek=None,
                bank_millions=0.0,
                squad_value_millions=0.0,
                free_transfers=0,
                chips_available=[],
                chip_played=None,
                overall_rank=None,
                total_points=0,
                optimization_error=(
                    "No squad state found yet. Run 'sync my squad' "
                    "(POST /api/v1/squad/sync/{gameweek}) before viewing the dashboard."
                ),
            )

        player_ids = [sp.player_id for sp in squad_state.players]
        players_by_id = self._load_players(db, player_ids)
        teams_by_id = self._load_teams(db)

        predictions = self._load_predictions(db, gameweek, player_ids)
        has_predictions = bool(predictions)

        squad_rows = self._build_squad_rows(
            squad_state, players_by_id, teams_by_id, predictions
        )
        injury_alerts = self._build_injury_alerts(players_by_id)
        fixture_ticker = self._build_fixture_ticker(
            db, squad_state, players_by_id, gameweek
        )

        projected_points = round(
            sum(
                row.predicted_points
                for row in squad_rows
                if row.is_starting and row.predicted_points is not None
            ),
            2,
        )
        # Captain's points count double - add the extra multiplier on top
        # of the base sum above, exactly like FPL's own scoring rule.
        starting_with_points = [
            row
            for row in squad_rows
            if row.is_starting and row.predicted_points is not None
        ]
        captain_row = max(
            starting_with_points, key=lambda r: r.predicted_points, default=None
        )
        captain = None
        vice_captain = None
        if captain_row is not None:
            projected_points = round(projected_points + captain_row.predicted_points, 2)
            captain = CaptainSuggestion(
                player_id=captain_row.player_id,
                web_name=captain_row.web_name,
                predicted_points=captain_row.predicted_points,
                reasoning=(
                    f"{captain_row.web_name} has the highest projected points "
                    f"({captain_row.predicted_points:.1f}) among your starting XI "
                    f"for gameweek {gameweek}."
                ),
            )
            runner_up = sorted(
                (r for r in starting_with_points if r.player_id != captain_row.player_id),
                key=lambda r: r.predicted_points,
                reverse=True,
            )
            if runner_up:
                vc_row = runner_up[0]
                vice_captain = CaptainSuggestion(
                    player_id=vc_row.player_id,
                    web_name=vc_row.web_name,
                    predicted_points=vc_row.predicted_points,
                    reasoning=(
                        f"{vc_row.web_name} is the next-best projected scorer "
                        "and covers the captaincy if your captain doesn't play."
                    ),
                )

        optimization: OptimizationResult | None = None
        optimization_error: str | None = None
        if has_predictions:
            try:
                optimization = self._optimizer.optimize(
                    db, gameweek=gameweek, horizon=1, max_transfers=2
                )
            except OptimizationError as exc:
                optimization_error = str(exc)
        else:
            optimization_error = (
                f"No DELPHI predictions found for gameweek {gameweek} yet. "
                "Generate them first (POST /api/v1/predictions/generate/"
                f"{gameweek}) to see transfer suggestions and projected points."
            )

        return DashboardView(
            gameweek=gameweek,
            generated_at=datetime.now(timezone.utc),
            has_squad=True,
            squad_gameweek=squad_state.gameweek,
            bank_millions=squad_state.bank_balance / 10,
            squad_value_millions=squad_state.squad_value / 10,
            free_transfers=squad_state.free_transfers,
            chips_available=list(squad_state.chips_available or []),
            chip_played=squad_state.chip_played,
            overall_rank=squad_state.overall_rank,
            total_points=squad_state.total_points,
            squad_rows=squad_rows,
            injury_alerts=injury_alerts,
            fixture_ticker=fixture_ticker,
            projected_points=projected_points,
            captain=captain,
            vice_captain=vice_captain,
            optimization=optimization,
            optimization_error=optimization_error,
            has_predictions=has_predictions,
        )

    # --- Data loading -----------------------------------------------------

    @staticmethod
    def _load_squad_state(db: Session, gameweek: int) -> SquadState | None:
        return (
            db.execute(
                select(SquadState)
                .where(SquadState.gameweek <= gameweek)
                .order_by(SquadState.gameweek.desc())
            )
            .scalars()
            .first()
        )

    @staticmethod
    def _load_players(db: Session, player_ids: list[int]) -> dict[int, Player]:
        if not player_ids:
            return {}
        rows = db.execute(select(Player).where(Player.id.in_(player_ids))).scalars().all()
        return {p.id: p for p in rows}

    @staticmethod
    def _load_teams(db: Session) -> dict[int, Team]:
        rows = db.execute(select(Team)).scalars().all()
        return {t.id: t for t in rows}

    @staticmethod
    def _load_predictions(
        db: Session, gameweek: int, player_ids: list[int]
    ) -> dict[int, float]:
        if not player_ids:
            return {}
        rows = db.execute(
            select(Prediction.player_id, Prediction.predicted_points).where(
                Prediction.gameweek == gameweek,
                Prediction.horizon == 1,
                Prediction.player_id.in_(player_ids),
            )
        ).all()
        return {player_id: float(points) for player_id, points in rows}

    # --- View building ------------------------------------------------------

    @staticmethod
    def _build_squad_rows(
        squad_state: SquadState,
        players_by_id: dict[int, Player],
        teams_by_id: dict[int, Team],
        predictions: dict[int, float],
    ) -> list[SquadRow]:
        rows: list[SquadRow] = []
        for sp in squad_state.players:
            player = players_by_id.get(sp.player_id)
            if player is None:
                continue
            team = teams_by_id.get(player.team_id)
            rows.append(
                SquadRow(
                    player_id=player.id,
                    web_name=player.web_name,
                    team_short_name=team.short_name if team else "???",
                    position=player.position.value,
                    price_millions=player.price_millions,
                    is_starting=sp.is_starting,
                    bench_position=sp.bench_position,
                    is_captain=sp.is_captain,
                    is_vice_captain=sp.is_vice_captain,
                    status=player.status.value,
                    news=player.news,
                    predicted_points=predictions.get(player.id),
                )
            )

        rows.sort(
            key=lambda r: (
                0 if r.is_starting else 1,
                _POSITION_ORDER.get(Position(r.position), 99),
                r.bench_position or 0,
                -(r.predicted_points or 0.0),
            )
        )
        return rows

    @staticmethod
    def _build_injury_alerts(players_by_id: dict[int, Player]) -> list[InjuryAlert]:
        alerts = [
            InjuryAlert(
                player_id=player.id,
                web_name=player.web_name,
                status=player.status.value,
                chance_of_playing_next_round=player.chance_of_playing_next_round,
                news=player.news,
            )
            for player in players_by_id.values()
            if player.status != InjuryStatus.AVAILABLE
        ]
        alerts.sort(key=lambda a: a.web_name)
        return alerts

    @staticmethod
    def _build_fixture_ticker(
        db: Session,
        squad_state: SquadState,
        players_by_id: dict[int, Player],
        gameweek: int,
    ) -> list[FixtureTickerEntry]:
        team_ids = {p.team_id for p in players_by_id.values()}
        if not team_ids:
            return []

        upper_bound = gameweek + _FIXTURE_LOOKAHEAD - 1
        fixtures = (
            db.execute(
                select(Fixture).where(
                    Fixture.gameweek.is_not(None),
                    Fixture.gameweek >= gameweek,
                    Fixture.gameweek <= upper_bound,
                    (Fixture.home_team_id.in_(team_ids))
                    | (Fixture.away_team_id.in_(team_ids)),
                )
            )
            .scalars()
            .all()
        )

        teams_by_id = {t.id: t for t in db.execute(select(Team)).scalars().all()}

        entries: list[FixtureTickerEntry] = []
        for fixture in fixtures:
            for team_id in (fixture.home_team_id, fixture.away_team_id):
                if team_id not in team_ids:
                    continue
                is_home = team_id == fixture.home_team_id
                opponent_id = fixture.away_team_id if is_home else fixture.home_team_id
                opponent = teams_by_id.get(opponent_id)
                entries.append(
                    FixtureTickerEntry(
                        team_id=team_id,
                        team_short_name=teams_by_id[team_id].short_name
                        if team_id in teams_by_id
                        else "???",
                        gameweek=fixture.gameweek or gameweek,
                        opponent_short_name=opponent.short_name if opponent else "???",
                        is_home=is_home,
                        difficulty=fixture.difficulty_for(team_id),
                    )
                )

        entries.sort(key=lambda e: (e.team_short_name, e.gameweek))
        return entries
