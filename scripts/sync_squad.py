"""One-off / cron-friendly script to sync "My Squad" for a gameweek.

Usage:
    python -m scripts.sync_squad --gameweek 8

Requires FPL_TEAM_ID to be set in .env.
"""

from __future__ import annotations

import argparse
import asyncio

from loguru import logger

from app.core.config import settings
from app.core.logging import configure_logging
from app.db.session import init_db, session_scope
from app.services.fpl_api import FPLAPIClient, FPLAPIError
from app.services.squad import SquadSyncService


async def run(gameweek: int) -> None:
    if settings.fpl_team_id is None:
        logger.error("FPL_TEAM_ID is not set in .env - cannot sync a squad.")
        raise SystemExit(1)

    service = SquadSyncService()

    async with FPLAPIClient() as client:
        picks_payload = await client.get_entry_event_picks(
            entry_id=settings.fpl_team_id, event_id=gameweek
        )

    with session_scope() as db:
        result = service.sync_from_fpl_payloads(db, gameweek=gameweek, picks_payload=picks_payload)

    logger.info(
        "Synced squad for gw {}: {} created, {} updated, {} removed. "
        "Free transfers: {}. Chips available: {}. Chip played: {}.",
        result.gameweek,
        result.players_created,
        result.players_updated,
        result.players_removed,
        result.free_transfers,
        result.chips_available,
        result.chip_played,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync 'My Squad' from the FPL API.")
    parser.add_argument("--gameweek", type=int, required=True, help="Gameweek to sync picks for.")
    args = parser.parse_args()

    configure_logging()
    init_db()

    try:
        asyncio.run(run(gameweek=args.gameweek))
    except FPLAPIError as exc:
        logger.error("Squad sync failed: {}", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
