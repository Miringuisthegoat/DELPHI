"""One-off / cron-friendly script to generate DELPHI predictions.

Usage:
    python -m scripts.generate_predictions --gameweek 8
    python -m scripts.generate_predictions --gameweek 8 --horizons 1 3 5
    python -m scripts.generate_predictions --evaluate 7
"""

from __future__ import annotations

import argparse

from loguru import logger

from app.core.config import settings
from app.core.logging import configure_logging
from app.db.session import init_db, session_scope
from app.ml.engine import DelphiPredictionEngine


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate or evaluate DELPHI predictions.")
    parser.add_argument("--gameweek", type=int, default=None, help="Gameweek to predict.")
    parser.add_argument(
        "--horizons",
        type=int,
        nargs="*",
        default=list(settings.ml_default_horizons),
        help="Horizons (in gameweeks) to predict, e.g. --horizons 1 3 5.",
    )
    parser.add_argument(
        "--evaluate",
        type=int,
        default=None,
        metavar="GAMEWEEK",
        help="Backfill actual outcomes for this already-played gameweek instead of predicting.",
    )
    args = parser.parse_args()

    configure_logging()
    init_db()
    engine = DelphiPredictionEngine()

    if args.evaluate is not None:
        with session_scope() as db:
            summary = engine.evaluate_gameweek(db, gameweek=args.evaluate)
        logger.info(
            "Evaluated gw {}: {} predictions, MAE={}",
            summary.gameweek,
            summary.predictions_evaluated,
            summary.mean_absolute_error,
        )
        return

    if args.gameweek is None:
        parser.error("--gameweek is required unless --evaluate is given")

    with session_scope() as db:
        summary = engine.generate_for_gameweek(
            db, gameweek=args.gameweek, horizons=tuple(args.horizons)
        )

    logger.info(
        "Generated predictions for gw {} using the '{}' model: {} players, "
        "{} created, {} updated",
        summary.gameweek,
        summary.model_used,
        summary.players_processed,
        summary.predictions_created,
        summary.predictions_updated,
    )


if __name__ == "__main__":
    main()
