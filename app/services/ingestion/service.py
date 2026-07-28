"""
Phase 4: persist typed FPL API data into the database.

`DataIngestionService` is the single place that turns the typed Pydantic
schemas produced by `FPLAPIClient` (Phase 3) into rows in the `teams`,
`players`, `fixtures`, and `player_gameweek_stats` tables (Phase 2's
models), so the rest of the application (prediction engine, optimizer,
dashboard) only ever reads from the database, never from a live API call.

Design notes
------------
* **Upsert, never rebuild.** Every sync method loads existing rows by
  their natural FPL id and updates them in place if present, matching the
  project's core philosophy of incrementally evolving state rather than
  wiping and regenerating it. `PlayerGameweekStats` history is additive
  (each gameweek is written once and then rarely revisited), but is still
  upserted defensively so a re-run after a correction never creates
  duplicate rows.
* **Partial failure is expected and handled.** A single malformed player
  or a not-yet-classifiable position must not abort an entire sync of
  ~700 players. Each row is processed independently and failures are
  collected into the returned `IngestionResult` rather than raised.
* **No commits inside the service.** Methods flush (so autoincrement ids
  and constraint violations surface immediately) but leave committing to
  the caller - typically `app.db.session.session_scope()` - so a caller
  running multiple sync steps in one call gets one atomic transaction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy.orm import Session

from app.models.fixture import Fixture
from app.models.player import Player
from app.models.player_stats import PlayerGameweekStats
from app.models.team import Team
from app.schemas.fpl_bootstrap import FPLBootstrapStatic, FPLTeam
from app.schemas.fpl_element_summary import FPLElementSummary
from app.schemas.fpl_fixtures import FPLFixture
from app.schemas.fpl_live import FPLEventLive
from app.services.ingestion.mappers import (
    build_position_map,
    map_fixture,
    map_history_row,
    map_live_element,
    map_player,
    map_team,
)


@dataclass
class IngestionResult:
    """Outcome of syncing one collection (teams, players, fixtures, ...)."""

    created: int = 0
    updated: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def processed(self) -> int:
        """Total rows successfully written (created + updated)."""
        return self.created + self.updated

    def __repr__(self) -> str:  # pragma: no cover - repr only
        return (
            f"IngestionResult(created={self.created}, updated={self.updated}, "
            f"failed={self.failed})"
        )


@dataclass
class FullSyncSummary:
    """Aggregate result of a full bootstrap-static + fixtures sync."""

    teams: IngestionResult
    players: IngestionResult
    fixtures: IngestionResult
    started_at: datetime
    finished_at: datetime

    @property
    def duration_seconds(self) -> float:
        return (self.finished_at - self.started_at).total_seconds()


class DataIngestionService:
    """Persists typed FPL API payloads into the database.

    Stateless aside from the `Session` passed to each method call, so a
    single instance can be reused across scheduled jobs, API routes, and
    scripts without any shared mutable state to worry about.
    """

    def sync_teams(self, db: Session, teams: list[FPLTeam]) -> IngestionResult:
        """Upsert every `FPLTeam` from bootstrap-static into `teams`."""
        result = IngestionResult()
        for team in teams:
            try:
                fields = map_team(team)
                existing = db.get(Team, team.id)
                if existing is None:
                    db.add(Team(**fields))
                    result.created += 1
                else:
                    for key, value in fields.items():
                        if key != "id":
                            setattr(existing, key, value)
                    result.updated += 1
            except Exception as exc:  # noqa: BLE001 - collected, not swallowed
                result.failed += 1
                result.errors.append(f"team {team.id}: {exc}")
                logger.warning("Failed to sync team {}: {}", team.id, exc)

        db.flush()
        logger.info(
            "Team sync complete: {} created, {} updated, {} failed",
            result.created,
            result.updated,
            result.failed,
        )
        return result

    def sync_players(
        self, db: Session, bootstrap: FPLBootstrapStatic
    ) -> IngestionResult:
        """Upsert every `FPLPlayer` from bootstrap-static into `players`.

        Requires `bootstrap.element_types` (rather than just the player
        list) to resolve each player's `element_type` id to a `Position`.
        Teams referenced by `player.team` must already exist - call
        `sync_teams` first in the same sync run.
        """
        result = IngestionResult()
        position_map = build_position_map(bootstrap.element_types)

        for player in bootstrap.elements:
            try:
                fields = map_player(player, position_map)
                if fields is None:
                    result.failed += 1
                    result.errors.append(
                        f"player {player.id}: unrecognised element_type "
                        f"{player.element_type}"
                    )
                    continue

                existing = db.get(Player, player.id)
                if existing is None:
                    db.add(Player(**fields))
                    result.created += 1
                else:
                    for key, value in fields.items():
                        if key != "id":
                            setattr(existing, key, value)
                    result.updated += 1
            except Exception as exc:  # noqa: BLE001
                result.failed += 1
                result.errors.append(f"player {player.id}: {exc}")
                logger.warning("Failed to sync player {}: {}", player.id, exc)

        db.flush()
        logger.info(
            "Player sync complete: {} created, {} updated, {} failed",
            result.created,
            result.updated,
            result.failed,
        )
        return result

    def sync_fixtures(
        self, db: Session, fixtures: list[FPLFixture]
    ) -> IngestionResult:
        """Upsert fixtures into `fixtures`.

        Teams referenced by `team_h`/`team_a` must already exist - call
        `sync_teams` first in the same sync run.
        """
        result = IngestionResult()
        for fixture in fixtures:
            try:
                fields = map_fixture(fixture)
                existing = db.get(Fixture, fixture.id)
                if existing is None:
                    db.add(Fixture(**fields))
                    result.created += 1
                else:
                    for key, value in fields.items():
                        if key != "id":
                            setattr(existing, key, value)
                    result.updated += 1
            except Exception as exc:  # noqa: BLE001
                result.failed += 1
                result.errors.append(f"fixture {fixture.id}: {exc}")
                logger.warning("Failed to sync fixture {}: {}", fixture.id, exc)

        db.flush()
        logger.info(
            "Fixture sync complete: {} created, {} updated, {} failed",
            result.created,
            result.updated,
            result.failed,
        )
        return result

    def sync_player_history(
        self, db: Session, player_id: int, summary: FPLElementSummary
    ) -> IngestionResult:
        """Backfill one player's per-gameweek stats from `element-summary`.

        This is the accurate historical source (includes the fixture and
        price at the time), used for backfilling a season's worth of
        gameweeks for players of interest - not intended to be called for
        every player, every gameweek (that's `sync_gameweek_live`'s job).
        """
        result = IngestionResult()
        for row in summary.history:
            try:
                fields = map_history_row(player_id, row)
                existing = (
                    db.query(PlayerGameweekStats)
                    .filter_by(player_id=player_id, gameweek=row.round)
                    .one_or_none()
                )
                if existing is None:
                    db.add(PlayerGameweekStats(**fields))
                    result.created += 1
                else:
                    for key, value in fields.items():
                        if key not in ("player_id", "gameweek"):
                            setattr(existing, key, value)
                    result.updated += 1
            except Exception as exc:  # noqa: BLE001
                result.failed += 1
                result.errors.append(
                    f"player {player_id} gw {row.round}: {exc}"
                )
                logger.warning(
                    "Failed to sync history for player {} gw {}: {}",
                    player_id,
                    row.round,
                    exc,
                )

        db.flush()
        logger.info(
            "History sync for player {} complete: {} created, {} updated, {} failed",
            player_id,
            result.created,
            result.updated,
            result.failed,
        )
        return result

    def sync_gameweek_live(
        self, db: Session, gameweek: int, live: FPLEventLive
    ) -> IngestionResult:
        """Sync near-real-time stats for a gameweek from `event/{id}/live`.

        Price, ownership, and form aren't part of the live payload, so
        they're read from each player's already-synced `Player` row as a
        same-day approximation. Players with no corresponding `Player` row
        (i.e. `sync_players` hasn't run yet) are skipped and counted as
        failures - run a full bootstrap sync first.
        """
        result = IngestionResult()
        for element in live.elements:
            try:
                player = db.get(Player, element.id)
                if player is None:
                    result.failed += 1
                    result.errors.append(
                        f"player {element.id}: not found, run sync_players first"
                    )
                    continue

                fields = map_live_element(
                    element,
                    price_at_gameweek=player.now_cost,
                    ownership_percent=player.ownership_percent,
                    form=0.0,
                )
                fields["gameweek"] = gameweek

                existing = (
                    db.query(PlayerGameweekStats)
                    .filter_by(player_id=element.id, gameweek=gameweek)
                    .one_or_none()
                )
                if existing is None:
                    db.add(PlayerGameweekStats(**fields))
                    result.created += 1
                else:
                    for key, value in fields.items():
                        if key not in ("player_id", "gameweek"):
                            setattr(existing, key, value)
                    result.updated += 1
            except Exception as exc:  # noqa: BLE001
                result.failed += 1
                result.errors.append(f"player {element.id} gw {gameweek}: {exc}")
                logger.warning(
                    "Failed to sync live stats for player {} gw {}: {}",
                    element.id,
                    gameweek,
                    exc,
                )

        db.flush()
        logger.info(
            "Live sync for gw {} complete: {} created, {} updated, {} failed",
            gameweek,
            result.created,
            result.updated,
            result.failed,
        )
        return result

    def sync_full_bootstrap(
        self,
        db: Session,
        bootstrap: FPLBootstrapStatic,
        fixtures: list[FPLFixture],
    ) -> FullSyncSummary:
        """Sync teams, players, and fixtures in the correct dependency order.

        Teams must be written before players/fixtures (foreign keys), so
        this is the recommended entry point for a routine "download +
        persist everything" sync rather than calling the individual
        `sync_*` methods directly and risking the wrong order.
        """
        started_at = datetime.now(timezone.utc)

        teams_result = self.sync_teams(db, bootstrap.teams)
        players_result = self.sync_players(db, bootstrap)
        fixtures_result = self.sync_fixtures(db, fixtures)

        finished_at = datetime.now(timezone.utc)

        summary = FullSyncSummary(
            teams=teams_result,
            players=players_result,
            fixtures=fixtures_result,
            started_at=started_at,
            finished_at=finished_at,
        )
        logger.info(
            "Full bootstrap sync complete in {:.2f}s: {} teams, {} players, "
            "{} fixtures processed",
            summary.duration_seconds,
            teams_result.processed,
            players_result.processed,
            fixtures_result.processed,
        )
        return summary
