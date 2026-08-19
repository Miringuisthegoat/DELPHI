"""
Phase 12: `HistoricalIngestionService` - fetches, maps, matches, and
upserts one or more prior seasons into `player_gameweek_stats_historical`.

Follows the same upsert-by-natural-key, partial-failure-is-collected-not-
fatal, no-commit-inside-the-service conventions as
`app.services.ingestion.DataIngestionService` (Phase 4) - see that
module's docstring for the reasoning, which applies unchanged here.

HOTFIX: vaastav's `merged_gw.csv` lists double-gameweek fixtures as
*separate rows* - the same player and `GW`, but a different underlying
fixture, each with that single fixture's own stats (not the gameweek
total). Combined with `SessionLocal`'s `autoflush=False` (this project's
session default - see `app/db/session.py`), a naive "query the DB to see
if this key already exists" check inside the row loop can't see rows
added earlier in the *same* ingest run, since nothing's been flushed yet
- both DGW rows would be treated as new inserts and collide on the
`(season, source_name, gameweek)` unique constraint at `db.flush()`.

Fixed by tracking every key upserted *within this run* in an in-memory
dict (`_pending`), consulted before the database is queried at all. When
a second row for the same key shows up mid-run (a genuine double
gameweek), its numeric stats are **summed** into the first row rather
than overwriting or duplicating it - correctly reflecting that the
player's total contribution that gameweek came from two fixtures.
Identity/context fields (position, team_name, match info) are left as
whatever the first-seen row already set.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.player import Player
from app.models.player_stats_historical import HistoricalPlayerGameweekStats
from app.services.historical.fetcher import HistoricalDataFetcher, HistoricalFetchError
from app.services.historical.mappers import map_gw_row, resolve_columns
from app.services.historical.name_matcher import PlayerNameMatcher

# Numeric stat fields that should be SUMMED when the same
# (season, source_name, gameweek) key appears more than once within a
# single season's CSV - i.e. a double gameweek, where vaastav lists each
# fixture as its own row rather than pre-aggregating per gameweek.
_SUMMABLE_FIELDS: tuple[str, ...] = (
    "minutes",
    "goals_scored",
    "assists",
    "expected_goals",
    "expected_assists",
    "clean_sheets",
    "goals_conceded",
    "saves",
    "own_goals",
    "penalties_saved",
    "clearances_blocks_interceptions",
    "tackles",
    "recoveries",
    "defensive_contribution",
    "yellow_cards",
    "red_cards",
    "penalties_missed",
    "bonus",
    "bps",
    "total_points",
)


@dataclass
class HistoricalIngestionResult:
    """Outcome of ingesting one season."""

    season: str
    created: int = 0
    updated: int = 0
    merged_duplicate_gws: int = 0
    """Count of rows that were summed into an already-seen row within
    this same run, rather than created or updated as a distinct row -
    i.e. double-gameweek fixtures being combined (see module docstring).
    Not counted in `created`/`updated`."""
    failed: int = 0
    matched: int = 0
    unmatched: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def processed(self) -> int:
        return self.created + self.updated

    @property
    def match_rate(self) -> float:
        total = self.matched + self.unmatched
        return round(self.matched / total, 3) if total else 0.0

    def __repr__(self) -> str:  # pragma: no cover - repr only
        return (
            f"HistoricalIngestionResult(season={self.season!r}, "
            f"processed={self.processed}, merged_dgw={self.merged_duplicate_gws}, "
            f"failed={self.failed}, match_rate={self.match_rate:.0%})"
        )


class HistoricalIngestionService:
    """Ingests one or more historical seasons from vaastav/Fantasy-Premier-League."""

    def __init__(self, fetcher: HistoricalDataFetcher | None = None) -> None:
        self._fetcher = fetcher or HistoricalDataFetcher()

    def ingest_season(self, db: Session, season: str) -> HistoricalIngestionResult:
        """Fetch, map, match, and upsert one season, e.g. "2023-24".

        Args:
            db: Active SQLAlchemy session (caller commits, per this
                project's `session_scope()` convention).
            season: vaastav's season folder naming, e.g. "2023-24".
        """
        result = HistoricalIngestionResult(season=season)

        try:
            df = self._fetcher.fetch_season(season)
            columns = resolve_columns(df)
        except (HistoricalFetchError, ValueError) as exc:
            result.failed += 1
            result.errors.append(str(exc))
            logger.warning("Historical ingestion for season {} aborted: {}", season, exc)
            return result

        current_players = db.execute(select(Player)).scalars().all()
        matcher = PlayerNameMatcher(current_players)

        # Preload every row already persisted for this season, keyed by
        # natural key, so re-running a season updates in place. This is a
        # single query per season (not per-row), and is separate from the
        # in-run `_pending` cache below, which handles the intra-CSV
        # double-gameweek case that a DB-only check would miss under
        # autoflush=False.
        existing_rows = {
            (row.season, row.source_name, row.gameweek): row
            for row in db.execute(
                select(HistoricalPlayerGameweekStats).where(
                    HistoricalPlayerGameweekStats.season == season
                )
            )
            .scalars()
            .all()
        }

        # Keyed by (season, source_name, gameweek); tracks whichever
        # ORM object (new or existing) is currently "the" row for that
        # key within this run, so a second CSV row for the same key gets
        # summed into it instead of colliding at flush time.
        pending: dict[tuple[str, str, int], HistoricalPlayerGameweekStats] = {}

        for _, row in df.iterrows():
            try:
                fields = map_gw_row(row, columns, season)
            except Exception as exc:  # noqa: BLE001
                result.failed += 1
                result.errors.append(f"row parse failed: {exc}")
                continue

            key = (fields["season"], fields["source_name"], fields["gameweek"])

            if key in pending:
                # Double gameweek: sum this fixture's stats into the row
                # already staged for this player/gameweek this run.
                target = pending[key]
                for stat_field in _SUMMABLE_FIELDS:
                    setattr(
                        target,
                        stat_field,
                        getattr(target, stat_field) + fields[stat_field],
                    )
                result.merged_duplicate_gws += 1
                continue

            match = matcher.match(fields["source_name"])
            fields["matched_player_id"] = match.player_id
            fields["match_confidence"] = match.confidence
            fields["match_method"] = match.method

            if match.player_id is not None:
                result.matched += 1
            else:
                result.unmatched += 1

            try:
                existing = existing_rows.get(key)
                if existing is None:
                    new_row = HistoricalPlayerGameweekStats(**fields)
                    db.add(new_row)
                    pending[key] = new_row
                    result.created += 1
                else:
                    for field_name, value in fields.items():
                        setattr(existing, field_name, value)
                    pending[key] = existing
                    result.updated += 1
            except Exception as exc:  # noqa: BLE001
                result.failed += 1
                result.errors.append(
                    f"{fields['source_name']} gw {fields['gameweek']}: {exc}"
                )
                logger.warning(
                    "Failed to upsert historical row {} gw {} ({}): {}",
                    fields["source_name"],
                    fields["gameweek"],
                    season,
                    exc,
                )

        db.flush()
        logger.info(
            "Historical ingestion for season {} complete: {} created, {} updated, "
            "{} double-gameweek rows merged, {} failed, match rate {:.0%}",
            season,
            result.created,
            result.updated,
            result.merged_duplicate_gws,
            result.failed,
            result.match_rate,
        )
        return result

    def ingest_seasons(
        self, db: Session, seasons: list[str]
    ) -> list[HistoricalIngestionResult]:
        """Convenience wrapper: ingest several seasons in one call.

        Each season is a fully independent fetch/map/match/upsert - one
        season's `HistoricalFetchError` (e.g. a typo'd season string)
        doesn't abort the others, matching this project's usual
        partial-failure-is-collected philosophy.
        """
        return [self.ingest_season(db, season) for season in seasons]
