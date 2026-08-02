"""One-off / cron-friendly script running the full local weekly pipeline:
generate predictions -> (optionally evaluate the previous gameweek) ->
build the weekly report.

Does NOT sync live FPL data - run `scripts.sync_data` and
`scripts.sync_squad` first if this gameweek's data isn't fresh (see
`WeeklyPipelineService`'s docstring for why sync stays a separate step).

Usage:
    python -m scripts.run_weekly_pipeline --gameweek 8
    python -m scripts.run_weekly_pipeline --gameweek 8 --no-evaluate
    python -m scripts.run_weekly_pipeline --gameweek 8 --send
"""

from __future__ import annotations

import argparse

from loguru import logger

from app.core.config import settings
from app.core.logging import configure_logging
from app.db.session import init_db, session_scope
from app.services.pipeline import WeeklyPipelineService
from app.services.reporting import ConsoleDeliveryChannel


def main() -> None:
    parser = argparse.ArgumentParser(description="Run DELPHI's full weekly pipeline.")
    parser.add_argument("--gameweek", type=int, required=True, help="Gameweek to plan for.")
    parser.add_argument(
        "--horizons",
        type=int,
        nargs="*",
        default=list(settings.ml_default_horizons),
        help="Horizons to (re)generate predictions for.",
    )
    parser.add_argument(
        "--no-evaluate",
        action="store_true",
        help="Skip backfilling actual outcomes for gameweek-1.",
    )
    parser.add_argument(
        "--send", action="store_true", help="Also deliver the report via the console channel."
    )
    args = parser.parse_args()

    configure_logging()
    init_db()
    pipeline = WeeklyPipelineService()

    with session_scope() as db:
        result = pipeline.run(
            db,
            gameweek=args.gameweek,
            horizons=tuple(args.horizons),
            evaluate_previous=not args.no_evaluate,
        )

    print(result.report.to_markdown())
    logger.info(
        "Pipeline for gw {} done in {:.2f}s: {} model, {} created / {} updated predictions",
        result.gameweek,
        result.duration_seconds,
        result.generation.model_used,
        result.generation.predictions_created,
        result.generation.predictions_updated,
    )
    if result.evaluation is not None:
        logger.info(
            "Evaluated {} predictions from gw {}",
            result.evaluation.predictions_evaluated,
            args.gameweek - 1,
        )

    if args.send:
        delivery = ConsoleDeliveryChannel().send(result.report)
        logger.info("Delivered via {}: {}", delivery.channel, delivery.detail)


if __name__ == "__main__":
    main()
