"""Phase 12: one-off / occasional script to pull prior FPL seasons from
vaastav/Fantasy-Premier-League into the local database.

Usage:
    python -m scripts.sync_historical_data --seasons 2021-22 2022-23 2023-24

Not part of the weekly pipeline or scheduler - past seasons never change,
so this only needs to be (re)run when you want to add another season's
worth of pretraining data.
"""

from __future__ import annotations

import argparse

from loguru import logger

from app.core.logging import configure_logging
from app.db.session import init_db, session_scope
from app.services.historical import HistoricalIngestionService


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sync prior-season FPL data from vaastav/Fantasy-Premier-League."
    )
    parser.add_argument(
        "--seasons",
        type=str,
        nargs="+",
        required=True,
        metavar="SEASON",
        help="Season folder names as used by the source repo, e.g. 2021-22 2022-23 2023-24.",
    )
    args = parser.parse_args()

    configure_logging()
    init_db()
    service = HistoricalIngestionService()

    with session_scope() as db:
        results = service.ingest_seasons(db, seasons=args.seasons)

    total_processed = 0
    total_failed = 0
    for result in results:
        logger.info(
            "{}: {} processed ({} created, {} updated), {} failed, "
            "match rate {:.0%} ({} matched / {} unmatched)",
            result.season,
            result.processed,
            result.created,
            result.updated,
            result.failed,
            result.match_rate,
            result.matched,
            result.unmatched,
        )
        if result.errors:
            for err in result.errors[:5]:
                logger.warning("  {}: {}", result.season, err)
            if len(result.errors) > 5:
                logger.warning(
                    "  {}: ...and {} more errors", result.season, len(result.errors) - 5
                )
        total_processed += result.processed
        total_failed += result.failed

    logger.info(
        "Historical sync complete: {} total rows processed across {} season(s), "
        "{} failed. Run `python -m scripts.train_model` next to retrain DELPHI "
        "with this pretraining data.",
        total_processed,
        len(results),
        total_failed,
    )


if __name__ == "__main__":
    main()
