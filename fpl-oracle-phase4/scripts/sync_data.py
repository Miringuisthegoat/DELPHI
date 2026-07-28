"""One-off / cron-friendly script to download and persist FPL data.

Usage:
    python -m scripts.sync_data                  # full sync: teams, players, fixtures
    python -m scripts.sync_data --gameweek 8      # also pull live stats for gw 8
    python -m scripts.sync_data --history 1 2 3   # also backfill history for these player ids
"""

from __future__ import annotations

import argparse
import asyncio

from loguru import logger

from app.core.logging import configure_logging
from app.db.session import init_db, session_scope
from app.services.fpl_api import FPLAPIClient, FPLAPIError
from app.services.ingestion import DataIngestionService


async def run(gameweek: int | None, history_player_ids: list[int]) -> None:
    """Fetch from the live FPL API and persist everything into the database."""
    service = DataIngestionService()

    async with FPLAPIClient() as client:
        logger.info("Fetching bootstrap-static and fixtures from the FPL API...")
        bootstrap = await client.get_bootstrap_static()
        fixtures = await client.get_fixtures()

        with session_scope() as db:
            summary = service.sync_full_bootstrap(db, bootstrap, fixtures)
        logger.info(
            "Bootstrap sync done in {:.2f}s: {} teams, {} players, {} fixtures "
            "({} failures)",
            summary.duration_seconds,
            summary.teams.processed,
            summary.players.processed,
            summary.fixtures.processed,
            summary.teams.failed + summary.players.failed + summary.fixtures.failed,
        )

        if gameweek is not None:
            logger.info("Fetching live stats for gameweek {}...", gameweek)
            live = await client.get_event_live(gameweek)
            with session_scope() as db:
                live_result = service.sync_gameweek_live(db, gameweek=gameweek, live=live)
            logger.info("Live sync for gw {}: {}", gameweek, live_result)

        for player_id in history_player_ids:
            logger.info("Backfilling history for player {}...", player_id)
            player_summary = await client.get_element_summary(player_id)
            with session_scope() as db:
                history_result = service.sync_player_history(
                    db, player_id=player_id, summary=player_summary
                )
            logger.info("History sync for player {}: {}", player_id, history_result)


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync FPL data into the local database.")
    parser.add_argument(
        "--gameweek",
        type=int,
        default=None,
        help="If given, also sync live stats for this gameweek.",
    )
    parser.add_argument(
        "--history",
        type=int,
        nargs="*",
        default=[],
        metavar="PLAYER_ID",
        help="If given, also backfill full-season history for these player ids.",
    )
    args = parser.parse_args()

    configure_logging()
    init_db()

    try:
        asyncio.run(run(gameweek=args.gameweek, history_player_ids=args.history))
    except FPLAPIError as exc:
        logger.error("Sync failed: {}", exc)
        raise SystemExit(1) from exc

    logger.info("Sync complete.")


if __name__ == "__main__":
    main()
